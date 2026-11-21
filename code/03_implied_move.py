"""
Step 3 - What the options market implies.

For each name with an event in the window:
  1. Raw implied move = ATM straddle (call mid + put mid, interpolated to spot)
     on the first expiry that captures the earnings reaction, as % of spot.
     This is the number most desks quote.
  2. Event-only implied move. The raw straddle also pays for the ordinary
     (non-event) days between now and expiry, so we strip them out using the
     ATM term structure (time in trading days, so Thanksgiving does not distort it):
        IV_pre  = ATM IV of the last expiry BEFORE the report      (N_pre days)
        IV_post = ATM IV of the first expiry AFTER the report      (N_post days)
        IV_2    = ATM IV of the expiry after that                  (N_2 days)
        fwd var (ex-event, post -> next expiry):
              sf^2 = (IV_2^2 * N_2 - IV_post^2 * N_post) / (N_2 - N_post)
        event var (central, "piecewise"):
              vE = [IV_post^2 * N_post - IV_pre^2 * N_pre - sf^2 * (N_post - N_pre - 1)] / 252
        event E|move| = sqrt(vE) * sqrt(2/pi)                       (normal approximation)
     Two bounds are also stored:
        low  ("pre-IV"):  all non-event days at IV_pre  (front-end vol is very high right now,
                          so this attributes the most to diffusion -> smallest event move)
        high ("fwd-IV"):  all non-event days at the forward vol sf
  3. Skew = 25-delta put IV minus 25-delta call IV on the post-event expiry.
     We also measure it on the pre-event expiry (no earnings inside it). The
     difference ("event increment") is the part of the skew the market attaches
     to the earnings print itself, as opposed to general crash risk.
     We compute our own IVs from bid/ask mids (Black-Scholes, r = 13-week T-bill,
     q = dividend yield) using OTM options only, compute each strike's delta from
     its own IV and interpolate to |delta| = 0.25.

Snapshot note: quotes are the Friday 2026-11-20 closing marks as served by
yfinance over the weekend. Mid prices require bid > 0 and ask >= bid.

Outputs
  data/implied.csv                       one row per event
  data/smiles/<TICKER>_<expiry>.csv      per-strike mids, IVs and deltas
"""
import warnings
import numpy as np
import pandas as pd
import yfinance as yf

from config import AS_OF, DATA
from utils import trading_days, next_trading_day, implied_vol, bs_delta, spot_close

warnings.filterwarnings("ignore")
(DATA / "smiles").mkdir(exist_ok=True)


def risk_free():
    try:
        return float(yf.Ticker("^IRX").history(period="10d")["Close"].iloc[-1]) / 100
    except Exception as e:
        print(f"^IRX failed ({e}); using 4.0% (ASSUMPTION)")
        return 0.04


def clean_mid(df):
    ok = (df.bid > 0) & (df.ask >= df.bid)
    df = df.copy()
    df["mid"] = np.where(ok, (df.bid + df.ask) / 2, np.nan)
    df["rel_spread"] = np.where(ok, (df.ask - df.bid) / df["mid"], np.nan)
    return df


def smile(tk, expiry, S, r, q):
    """Per-strike table with call/put mids, OTM IV and delta for one expiry."""
    oc = yf.Ticker(tk).option_chain(expiry)
    c = clean_mid(oc.calls)[["strike", "bid", "ask", "mid", "rel_spread", "openInterest"]]
    p = clean_mid(oc.puts)[["strike", "bid", "ask", "mid", "rel_spread", "openInterest"]]
    m = c.merge(p, on="strike", how="outer", suffixes=("_c", "_p")).sort_values("strike")
    N = trading_days(AS_OF, expiry)
    T = N / 252
    m["iv_c"] = [implied_vol(x, S, k, T, r, q, "c") for x, k in zip(m.mid_c, m.strike)]
    m["iv_p"] = [implied_vol(x, S, k, T, r, q, "p") for x, k in zip(m.mid_p, m.strike)]
    # IVs at bid and ask too, so we can say how wide the skew quote really is
    for side, kind in (("c", "c"), ("p", "p")):
        for px in ("bid", "ask"):
            col = m[f"{px}_{side}"].where(m[f"bid_{side}"] > 0)
            m[f"iv_{side}_{px}"] = [implied_vol(x, S, k, T, r, q, kind) for x, k in zip(col, m.strike)]
    # OTM convention: puts below spot, calls at/above spot
    m["otm_iv"] = np.where(m.strike < S, m.iv_p, m.iv_c)
    m["otm_iv_bid"] = np.where(m.strike < S, m.iv_p_bid, m.iv_c_bid)
    m["otm_iv_ask"] = np.where(m.strike < S, m.iv_p_ask, m.iv_c_ask)
    m["otm_kind"] = np.where(m.strike < S, "p", "c")
    m["delta"] = [bs_delta(S, k, T, v, r, q, kd) if np.isfinite(v) else np.nan
                  for k, v, kd in zip(m.strike, m.otm_iv, m.otm_kind)]
    m["N_days"] = N
    m.to_csv(DATA / "smiles" / f"{tk}_{expiry}.csv", index=False)
    return m, T, N


def atm(m, S):
    """Interpolate straddle mid and ATM IV to the spot price."""
    m = m.dropna(subset=["mid_c", "mid_p"])
    m = m.assign(straddle=m.mid_c + m.mid_p, iv_atm=(m.iv_c + m.iv_p) / 2)
    lo = m[m.strike <= S].iloc[-1]
    hi = m[m.strike > S].iloc[0]
    w = (S - lo.strike) / (hi.strike - lo.strike)
    return ((1 - w) * lo.straddle + w * hi.straddle,
            (1 - w) * lo.iv_atm + w * hi.iv_atm,
            (lo.strike, hi.strike),
            np.nanmean([lo.rel_spread_c, lo.rel_spread_p, hi.rel_spread_c, hi.rel_spread_p]))


def iv_at_delta(m, target, col="otm_iv"):
    """Linear interpolation of OTM IV (mid, bid or ask column) at a delta (-0.25 puts, +0.25 calls)."""
    side = m[(m.otm_kind == ("p" if target < 0 else "c")) & m.delta.notna() & m[col].notna()]
    side = side.sort_values("delta")
    if len(side) < 2 or not (side.delta.min() <= target <= side.delta.max()):
        # fallback: closest strike to the target delta
        if len(side) == 0:
            return np.nan, np.nan, "missing"
        row = side.iloc[(side.delta - target).abs().argmin()]
        return row[col], row.strike, "closest strike"
    iv = np.interp(target, side.delta, side[col])
    k = np.interp(target, side.delta, side.strike)
    return iv, k, "interpolated"


def rr_halfwidth(m):
    """Half the bid/ask width of the 25d risk reversal, in vol points."""
    w_p = iv_at_delta(m, -0.25, "otm_iv_ask")[0] - iv_at_delta(m, -0.25, "otm_iv_bid")[0]
    w_c = iv_at_delta(m, 0.25, "otm_iv_ask")[0] - iv_at_delta(m, 0.25, "otm_iv_bid")[0]
    return 0.5 * (w_p + w_c)


def main():
    cal = pd.read_csv(DATA / "calendar.csv", parse_dates=["next_event"])
    events = cal[cal.in_window]
    r = risk_free()
    rows = []
    for _, e in events.iterrows():
        tk = e.ticker
        try:
            t = yf.Ticker(tk)
            S = spot_close(tk, AS_OF)
            info = t.info
            q = (info.get("dividendRate") or 0) / S
            ev = e.next_event.date()
            react = ev if e.timing == "BMO" else next_trading_day(ev)
            exps = [pd.Timestamp(x).date() for x in t.options]
            post = min(x for x in exps if x >= react)
            pre_c = [x for x in exps if AS_OF < x <= ev and (e.timing != "BMO" or x < ev)]
            pre = max(pre_c) if pre_c else None

            m_post, T_post, N_post = smile(tk, str(post), S, r, q)
            strad, iv_post, ks, spr = atm(m_post, S)
            raw_move = strad / S

            if pre is not None:
                m_pre, T_pre, N_pre = smile(tk, str(pre), S, r, q)
                _, iv_pre, _, _ = atm(m_pre, S)
                # skew on the pre-event expiry = the market's "no-event" skew (crash risk, leverage effect)
                rr_pre = iv_at_delta(m_pre, -0.25)[0] - iv_at_delta(m_pre, 0.25)[0]
            else:
                iv_pre, N_pre, rr_pre = np.nan, 0, np.nan
            post2 = min(x for x in exps if x > post)
            m_2, _, N_2 = smile(tk, str(post2), S, r, q)
            _, iv_2, _, _ = atm(m_2, S)
            fwd_var = (iv_2 ** 2 * N_2 - iv_post ** 2 * N_post) / (N_2 - N_post)   # annualized
            iv_fwd = np.sqrt(fwd_var) if fwd_var > 0 else np.nan
            tot = iv_post ** 2 * N_post / 252

            def ev_move(v):  # event variance -> (sigma, E|move|)
                return (np.sqrt(v), np.sqrt(v) * np.sqrt(2 / np.pi)) if v > 0 else (np.nan, np.nan)

            pre_part = (iv_pre ** 2 * N_pre / 252) if pre is not None else 0.0
            n_mid = N_post - N_pre - 1
            sig_c, mv_c = ev_move(tot - pre_part - (fwd_var if np.isfinite(iv_fwd) else iv_pre ** 2) * n_mid / 252)
            sig_a, mv_a = ev_move(tot - iv_pre ** 2 * (N_post - 1) / 252)
            sig_b, mv_b = ev_move(tot - fwd_var * (N_post - 1) / 252) if np.isfinite(iv_fwd) else (np.nan, np.nan)
            sig_ev, ev_move_c = sig_c, mv_c

            iv_p25, k_p25, how_p = iv_at_delta(m_post, -0.25)
            iv_c25, k_c25, how_c = iv_at_delta(m_post, 0.25)
            rows.append(dict(
                ticker=tk, event_date=ev, timing=e.timing, reaction_date=react,
                spot=round(S, 2), r=round(r, 4), q=round(q, 4),
                pre_expiry=pre, N_pre=N_pre, iv_pre_atm=round(iv_pre * 100, 2),
                post_expiry=post, N_post=N_post, iv_post_atm=round(iv_post * 100, 2),
                atm_strikes=f"{ks[0]:g}/{ks[1]:g}", atm_rel_spread=round(spr, 3),
                straddle=round(strad, 2), straddle_pct=round(raw_move * 100, 2),
                next_expiry=post2, N_2=N_2, iv_2_atm=round(iv_2 * 100, 2), iv_fwd_ex_event=round(iv_fwd * 100, 2),
                event_sigma_pct=round(sig_ev * 100, 2), implied_event_move_pct=round(ev_move_c * 100, 2),
                implied_event_move_low_pct=round(mv_a * 100, 2), implied_event_move_high_pct=round(mv_b * 100, 2),
                iv_put25=round(iv_p25 * 100, 2), k_put25=round(k_p25, 1),
                iv_call25=round(iv_c25 * 100, 2), k_call25=round(k_c25, 1),
                rr25_put_minus_call=round((iv_p25 - iv_c25) * 100, 2),
                rr25_halfwidth=round(rr_halfwidth(m_post) * 100, 2),
                rr25_pre_expiry=round(rr_pre * 100, 2),
                rr25_event_increment=round((iv_p25 - iv_c25 - rr_pre) * 100, 2),
                skew_method=f"put {how_p} / call {how_c}",
                source="yfinance option chain, Friday 11/20 close marks",
            ))
        except Exception as ex:
            print(f"[{tk}] implied move FAILED: {ex}")
            rows.append(dict(ticker=tk, event_date=e.next_event.date(), source=f"FAILED: {ex}"))
    out = pd.DataFrame(rows)
    out.to_csv(DATA / "implied.csv", index=False)
    print(out.drop(columns=["source"]).T.to_string())


if __name__ == "__main__":
    main()
