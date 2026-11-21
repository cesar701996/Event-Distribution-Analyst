"""
Steps 6 + 7 - Our forecast, the comparison with the market, and the decision.

-------------------------------------------------------------------------------
A. MOVE SIZE  (expected absolute 1-day earnings move, % of spot)
-------------------------------------------------------------------------------
  1. Base rate      B = 0.6 * mean|move| (last 12) + 0.4 * mean|move| (last 4)
  2. Regime         Ordinary daily vol is far above normal right now. We treat the
                    event-day variance as  earnings-information variance + one ordinary
                    day's variance, and only swap the ordinary day for today's level:
                        sig_hist = B * sqrt(pi/2)                   (E|x| -> std, normal approx)
                        info_var = sig_hist^2 - d_hist^2            d_hist = median pre-event 20d RV / sqrt(252)
                        sig_now  = sqrt(info_var + d_now^2)         d_now  = current 20d RV / sqrt(252)
                        regime   = sig_now / sig_hist
                    Scaling the WHOLE move by a vol ratio is not supported by our data:
                    across 144 past events the fitted exponent is ~0 (stored in
                    data/forecast_diagnostics.json).
  3. Alt data       Google Trends attention multiplier from step 5 (bounded 0.90-1.10).
  4. Fundamentals   uncertainty_mult from analyst_views.json (0.9 / 1.0 / 1.1).
     Forecast E|move| = B * regime * alt * fundamental

-------------------------------------------------------------------------------
B. DIRECTION AND SHAPE
-------------------------------------------------------------------------------
  p_hist = (n_up + 1) / (n + 2)                       Laplace-smoothed share of up moves
  p_up   = 0.5 + 0.5*(p_hist - 0.5) + 0.03 * direction * confidence      (clipped 0.25-0.75)
  Up / down sizes = historical mean up / down move, shrunk 50% toward mean|move|, then
  scaled together so that p_up*m_up + (1-p_up)*m_down = forecast E|move|.
  Event jump J: +|N(0, s_up)| with prob p_up, -|N(0, s_down)| otherwise (s = m*sqrt(pi/2)).
  Our event skewness g_E(ours) = skewness of J (Monte Carlo).

-------------------------------------------------------------------------------
C. SKEW COMPARISON  (Gram-Charlier + cumulant additivity)
-------------------------------------------------------------------------------
  Gram-Charlier (Backus, Foresi & Wu 2004) links the 25-delta risk reversal to the
  skewness g of the log return over the option's life:
        RR25 (put - call, vol pts)  ~=  -0.2247 * ATM_IV * g      (0.2247 = 2*0.674/6)
  Variance (k2) and third cumulant (k3) ADD across independent pieces, so:
    pre-event expiry (no event):   g_pre  from RR_pre ;  k3_pre = g_pre * k2_pre^1.5
    ordinary days to post expiry:  k3_diff = k3_pre * V_d / k2_pre   (k3 scales with time like k2)
    post-event expiry:             k3_post = k3_diff + k3_event ;  k2_post = V_d + sig_E^2
  Market-implied event skewness:   g_E(mkt) = (k3_post(from RR_post) - k3_diff) / sig_E^3
  Our model RR:   put g_E(ours) into the same formula with the market's own sig_E and
                  diffusion, so the gap reflects only the SHAPE of the event.
  Skew gap = RR_model(ours) - RR_market.  Positive gap = market puts look cheap vs our view.

-------------------------------------------------------------------------------
D. DECISION  (thresholds in config.py; set before looking at results)
-------------------------------------------------------------------------------
  vol flag  : |forecast - implied| / implied >= 20%, gap >= 1.0 pt, AND forecast lies
              outside the implied-move range from the three term-structure methods
  skew flag : |skew gap| >= 3 vol pts AND larger than the RR's own bid/ask half-width
  dir flag  : confidence >= 4

-------------------------------------------------------------------------------
E. TRADE ECONOMICS  (inputs to the trade write-ups)
-------------------------------------------------------------------------------
  Monte Carlo of our event jump + ordinary-day lognormal diffusion at the market's own
  ex-event vol (real-world, no risk-neutral recentering):
   * Friday marks: EV and win rate of a short straddle and a short iron fly (+/-15% wings)
     on the post-event expiry
   * Report-day entry: projected straddle if entered at the close on report day (event +
     remaining ordinary days at the market's forward vol), the share of past moves inside
     it, and our probability / EV.

Outputs: data/forecast.csv, data/trade_econ.csv, data/forecast_diagnostics.json
"""
import json
import warnings
import numpy as np
import pandas as pd
import yfinance as yf
from scipy import optimize, stats

from config import (AS_OF, DATA, W_FULL, W_RECENT, DIR_TILT_PER_CONF, P_UP_CAP, MAG_SHRINK,
                    VOL_GAP_REL, VOL_GAP_ABS, SKEW_GAP_VOLS, DIR_CONF_MIN, MC_PATHS, MC_SEED, WING_PCT)
from utils import trading_days, spot_close

warnings.filterwarnings("ignore")
SQ = np.sqrt(np.pi / 2)          # std / E|x| for a normal
GC = 2 * 0.674 / 6               # Gram-Charlier RR25 coefficient (~0.2247)


# ------------------------------------------------------------------ helpers
def current_rv20(tickers):
    """Annualized realized vol (%) of the last 20 sessions ending AS_OF (incl. the AS_OF close)."""
    px = yf.download(tickers, start=str(AS_OF - pd.Timedelta(days=60)), end=str(AS_OF + pd.Timedelta(days=1)),
                     auto_adjust=True, progress=False)["Close"]
    px.index = px.index.tz_localize(None).date
    px = px.loc[[d for d in px.index if d <= AS_OF]]
    for tk in tickers:                       # weekend quirk: blank last bar -> use cached spot
        if AS_OF not in px.index or pd.isna(px.loc[AS_OF, tk]):
            px.loc[AS_OF, tk] = spot_close(tk, AS_OF)
    lr = np.log(px.sort_index().astype(float)).diff()
    return (lr.tail(20).std() * np.sqrt(252) * 100).to_dict()


def regime_calibration(moves, br):
    """Fit rel_move = c * (rv_pre / median rv_pre) ** beta across all past events (+ bootstrap)."""
    m = moves.copy()
    m["rel"] = m.abs_move_pct / m.ticker.map(br.mean_abs)
    m["rvr"] = m.rv20_pre / m.ticker.map(br.median_rv20_pre)
    fit = lambda d: optimize.minimize(lambda b: ((d.rel - b[0] * d.rvr ** b[1]) ** 2).sum(), [1.0, 0.5]).x
    c, beta = fit(m)
    rng = np.random.default_rng(0)
    boots = [fit(m.sample(len(m), replace=True, random_state=int(rng.integers(1e9))))[1] for _ in range(300)]
    return dict(n=len(m), beta=beta, beta_p05=np.percentile(boots, 5), beta_p95=np.percentile(boots, 95))


def event_jump(u, z, p_up, s_up, s_down):
    """Two-sided half-normal mixture; u ~ U(0,1), z ~ |N(0,1)|."""
    return np.where(u < p_up, z * s_up, -z * s_down)


def rr_from_skew(g, atm_iv_pct):
    return -GC * atm_iv_pct * g


def skew_from_rr(rr_pct, atm_iv_pct):
    return -rr_pct / (GC * atm_iv_pct)


# ------------------------------------------------------------------ main
def main():
    br = pd.read_csv(DATA / "base_rates.csv").set_index("ticker")
    moves = pd.read_csv(DATA / "earnings_moves.csv")
    imp = pd.read_csv(DATA / "implied.csv")
    alt = pd.read_csv(DATA / "alt" / "alt_scores.csv").set_index("ticker")
    views = json.loads((DATA.parent / "code" / "analyst_views.json").read_text())
    rv_now = current_rv20(list(imp.ticker))
    calib = regime_calibration(moves, br)
    rng = np.random.default_rng(MC_SEED)

    out, econ, skew_math = [], [], {}
    for _, im in imp.iterrows():
        tk = im.ticker
        b, v = br.loc[tk], views[tk]
        g = moves[moves.ticker == tk]
        S = im.spot

        # ---------------- A. move size
        B = W_FULL * b.mean_abs + W_RECENT * b.mean_abs_last4
        sig_hist = B / 100 * SQ
        d_hist = b.median_rv20_pre / 100 / np.sqrt(252)
        d_now = rv_now[tk] / 100 / np.sqrt(252)
        regime = np.sqrt(max(sig_hist ** 2 - d_hist ** 2, 0) + d_now ** 2) / sig_hist
        alt_mult = float(alt.loc[tk, "alt_mult"]) if tk in alt.index else 1.0
        fmult = float(v["uncertainty_mult"])
        fc = B * regime * alt_mult * fmult

        # ---------------- B. direction and shape
        p_hist = (int(b.n_up) + 1) / (int(b.n) + 2)
        p_up = float(np.clip(0.5 + 0.5 * (p_hist - 0.5) + DIR_TILT_PER_CONF * v["direction"] * v["confidence"],
                             *P_UP_CAP))
        mu_raw = (1 - MAG_SHRINK) * b.mean_up_move + MAG_SHRINK * b.mean_abs
        md_raw = (1 - MAG_SHRINK) * b.mean_down_move + MAG_SHRINK * b.mean_abs
        k = fc / (p_up * mu_raw + (1 - p_up) * md_raw)
        m_up, m_dn = mu_raw * k, md_raw * k
        exp_signed = p_up * m_up - (1 - p_up) * m_dn
        u = rng.random(MC_PATHS)
        z = np.abs(rng.standard_normal(MC_PATHS))
        J = event_jump(u, z, p_up, m_up / 100 * SQ, m_dn / 100 * SQ)
        g_E_ours = float(stats.skew(J))

        # ---------------- C. skew comparison
        ivp, ivf, ivpost = im.iv_pre_atm / 100, im.iv_fwd_ex_event / 100, im.iv_post_atm / 100
        k2_pre = ivp ** 2 * im.N_pre / 252
        V_d = (ivp ** 2 * im.N_pre + ivf ** 2 * (im.N_post - im.N_pre - 1)) / 252
        sig_E = im.event_sigma_pct / 100
        k2_post = ivpost ** 2 * im.N_post / 252                      # = V_d + sig_E^2 by construction
        g_pre = skew_from_rr(im.rr25_pre_expiry, im.iv_pre_atm)
        k3_diff = g_pre * k2_pre ** 1.5 * V_d / k2_pre
        g_post_mkt = skew_from_rr(im.rr25_put_minus_call, im.iv_post_atm)
        k3_E_mkt = g_post_mkt * k2_post ** 1.5 - k3_diff
        g_E_mkt = k3_E_mkt / sig_E ** 3
        rr_sym = rr_from_skew(k3_diff / k2_post ** 1.5, im.iv_post_atm)                 # symmetric event
        rr_ours = rr_from_skew((k3_diff + g_E_ours * sig_E ** 3) / k2_post ** 1.5, im.iv_post_atm)
        skew_gap = rr_ours - im.rr25_put_minus_call
        skew_math[tk] = dict(g_pre=g_pre, k2_pre=k2_pre, V_d=V_d, k3_diff=k3_diff, sig_E=sig_E,
                             k2_post=k2_post, g_post_mkt=g_post_mkt, k3_E_mkt=k3_E_mkt, g_E_mkt=g_E_mkt,
                             g_E_ours=g_E_ours, rr_symmetric_event=rr_sym, rr_ours=rr_ours)

        # ---------------- D. gaps and flags
        imp_mv = im.implied_event_move_pct
        gap_abs, gap_rel = fc - imp_mv, fc / imp_mv - 1
        outside = not (im.implied_event_move_low_pct <= fc <= im.implied_event_move_high_pct)
        vol_flag = abs(gap_rel) >= VOL_GAP_REL and abs(gap_abs) >= VOL_GAP_ABS and outside
        skew_flag = abs(skew_gap) >= SKEW_GAP_VOLS and abs(skew_gap) > im.rr25_halfwidth
        dir_flag = v["confidence"] >= DIR_CONF_MIN and v["direction"] != 0

        # ---------------- E. trade economics
        sm = pd.read_csv(DATA / "smiles" / f"{tk}_{im.post_expiry}.csv").dropna(subset=["mid_c", "mid_p"])
        Kc = sm.iloc[(sm.strike - S).abs().argmin()].strike
        row = sm[sm.strike == Kc].iloc[0]
        kp_w = sm.iloc[(sm.strike - S * (1 - WING_PCT)).abs().argmin()]
        kc_w = sm.iloc[(sm.strike - S * (1 + WING_PCT)).abs().argmin()]
        X = rng.standard_normal(MC_PATHS) * np.sqrt(V_d) - V_d / 2           # ordinary days to expiry
        ST = S * np.exp(J + X)
        disc = np.exp(-im.r * im.N_post / 252)
        strad = row.mid_c + row.mid_p
        fly_credit = strad - kc_w.mid_c - kp_w.mid_p
        strad_pnl = strad - disc * np.abs(ST - Kc)
        fly_pnl = fly_credit - disc * (np.abs(ST - Kc) - np.maximum(ST - kc_w.strike, 0)
                                       - np.maximum(kp_w.strike - ST, 0))
        # report-day entry (close of the report day, AMC): event + remaining ordinary days
        n_extra = trading_days(im.event_date, im.post_expiry) - 1
        v_extra = ivf ** 2 * n_extra / 252
        evday_strad = np.sqrt(sig_E ** 2 + v_extra) / SQ * 100
        tot = np.abs(J + rng.standard_normal(MC_PATHS) * np.sqrt(v_extra)) * 100
        econ.append(dict(
            ticker=tk, post_expiry=im.post_expiry, atm_strike=Kc, straddle_mid=round(strad, 2),
            straddle_pct_spot=round(strad / S * 100, 2),
            ev_short_straddle_pct=round(strad_pnl.mean() / S * 100, 2),
            win_short_straddle=round((strad_pnl > 0).mean(), 3),
            wing_put=kp_w.strike, wing_call=kc_w.strike, fly_credit=round(fly_credit, 2),
            fly_max_loss=round(max(kc_w.strike - Kc, Kc - kp_w.strike) - fly_credit, 2),
            fly_breakevens=f"{Kc - fly_credit:.1f} / {Kc + fly_credit:.1f}",
            ev_short_iron_fly_pct=round(fly_pnl.mean() / S * 100, 2),
            win_short_iron_fly=round((fly_pnl > 0).mean(), 3),
            evday_extra_days=n_extra, evday_projected_straddle_pct=round(evday_strad, 2),
            hist_moves_inside_evday_straddle=f"{int((g.abs_move_pct <= evday_strad).sum())}/{len(g)}",
            model_prob_inside_evday_straddle=round((tot <= evday_strad).mean(), 3),
            model_ev_evday_short_straddle_pct=round(evday_strad - tot.mean(), 2),
        ))

        out.append(dict(
            ticker=tk, event_date=im.event_date, spot=S,
            mean_abs_12=round(b.mean_abs, 2), mean_abs_last4=round(b.mean_abs_last4, 2),
            se_mean_abs=round(b.se_mean_abs, 2), base_move=round(B, 2),
            rv20_now=round(rv_now[tk], 1), rv20_pre_hist_median=round(b.median_rv20_pre, 1),
            regime_mult=round(regime, 3), alt_mult=round(alt_mult, 3), fund_mult=fmult,
            forecast_move_pct=round(fc, 2),
            implied_straddle_pct=im.straddle_pct, implied_event_move_pct=imp_mv,
            implied_event_low=im.implied_event_move_low_pct, implied_event_high=im.implied_event_move_high_pct,
            gap_pts=round(gap_abs, 2), gap_rel_pct=round(gap_rel * 100, 1), forecast_outside_implied_range=outside,
            direction={1: "up", 0: "neutral", -1: "down"}[v["direction"]], confidence=v["confidence"],
            p_hist_up=round(p_hist, 3), p_up=round(p_up, 3), m_up=round(m_up, 2), m_down=round(m_dn, 2),
            exp_signed_move=round(exp_signed, 2),
            market_rr25=im.rr25_put_minus_call, market_rr25_halfwidth=im.rr25_halfwidth,
            market_rr25_pre=im.rr25_pre_expiry, rr25_if_symmetric_event=round(rr_sym, 2),
            model_rr25_ours=round(rr_ours, 2), skew_gap_vols=round(skew_gap, 2),
            event_skewness_mkt=round(g_E_mkt, 2), event_skewness_ours=round(g_E_ours, 2),
            skew_view=("market puts rich vs our view" if skew_gap <= -SKEW_GAP_VOLS else
                       "market puts cheap vs our view" if skew_gap >= SKEW_GAP_VOLS else "in line"),
            vol_flag=vol_flag, skew_flag=skew_flag, dir_flag=dir_flag,
            trade_idea=bool(vol_flag or skew_flag or dir_flag),
            beats_last12=int((g.surprise_pct > 0).sum()),
        ))

    fc_df = pd.DataFrame(out)
    fc_df.to_csv(DATA / "forecast.csv", index=False)
    pd.DataFrame(econ).to_csv(DATA / "trade_econ.csv", index=False)
    diag = dict(regime_calibration=calib, skew_math=skew_math,
                thresholds=dict(vol_gap_rel=VOL_GAP_REL, vol_gap_abs=VOL_GAP_ABS,
                                skew_gap_vols=SKEW_GAP_VOLS, dir_conf_min=DIR_CONF_MIN))
    (DATA / "forecast_diagnostics.json").write_text(json.dumps(diag, indent=1, default=float))
    pd.set_option("display.width", 250)
    print(fc_df.T.to_string())
    print(pd.DataFrame(econ).T.to_string())
    print(json.dumps(diag, indent=1, default=float))


if __name__ == "__main__":
    main()
