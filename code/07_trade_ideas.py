"""
Step 7b - Price and score the trade structures for flagged names.

For every name that 06_forecast.py flagged and that has a "trade" block in
analyst_views.json:
  * price each leg at the Friday 11/20 mid on the post-event expiry
  * optional stock hedge to make the package delta-neutral at entry
  * payoff at expiry on a price grid -> max gain, max loss, breakevens
  * Monte Carlo under OUR distribution (our event jump + ordinary-day lognormal
    diffusion at the market's ex-event vol, no risk-neutral drift) -> win rate, EV
  * for skew trades, a rough edge = skew gap (vol pts) x average leg vega

This is analysis only. Nothing here sends orders.

Output: data/trade_ideas.csv
"""
import json
import numpy as np
import pandas as pd
from scipy.stats import norm

from config import DATA, MC_PATHS, MC_SEED

SQ = np.sqrt(np.pi / 2)


def bs_delta_vega(S, K, T, iv, r, q, kind):
    d1 = (np.log(S / K) + (r - q + 0.5 * iv ** 2) * T) / (iv * np.sqrt(T))
    delta = np.exp(-q * T) * (norm.cdf(d1) if kind == "c" else norm.cdf(d1) - 1)
    vega = S * np.exp(-q * T) * norm.pdf(d1) * np.sqrt(T) / 100        # per 1 vol point
    return delta, vega


def main():
    fc = pd.read_csv(DATA / "forecast.csv").set_index("ticker")
    imp = pd.read_csv(DATA / "implied.csv").set_index("ticker")
    diag = json.loads((DATA / "forecast_diagnostics.json").read_text())
    views = json.loads((DATA.parent / "code" / "analyst_views.json").read_text())
    rng = np.random.default_rng(MC_SEED + 1)
    rows = []
    for tk, f in fc.iterrows():
        if not f.trade_idea:
            continue
        spec = views.get(tk, {}).get("trade")
        if spec is None:
            print(f"[{tk}] flagged but no trade spec in analyst_views.json")
            continue
        im = imp.loc[tk]
        S, r, q, T = im.spot, im.r, im.q, im.N_post / 252
        sm = pd.read_csv(DATA / "smiles" / f"{tk}_{im.post_expiry}.csv").set_index("strike")
        legs, prem, delta, vega_abs = [], 0.0, 0.0, []
        for L in spec["legs"]:
            row = sm.loc[L["strike"]]
            mid = row[f"mid_{L['type']}"]
            iv = row[f"iv_{L['type']}"]
            d, vg = bs_delta_vega(S, L["strike"], T, iv, r, q, L["type"])
            prem -= L["qty"] * mid                  # + = credit received
            delta += L["qty"] * d
            vega_abs.append(vg)
            legs.append(f"{'+' if L['qty'] > 0 else ''}{L['qty']:g} {L['strike']:g}{L['type'].upper()} @ {mid:.2f} (IV {iv*100:.0f}%, delta {d:+.2f})")
        stock = -delta if spec.get("hedge_delta") else 0.0

        def pnl(ST):
            ST = np.asarray(ST, dtype=float)
            val = np.zeros_like(ST)
            for L in spec["legs"]:
                pay = np.maximum(ST - L["strike"], 0) if L["type"] == "c" else np.maximum(L["strike"] - ST, 0)
                val += L["qty"] * pay
            return prem + val + stock * (ST - S)

        grid = np.linspace(0.3 * S, 1.8 * S, 30001)
        pg = pnl(grid)
        sign = np.sign(pg)
        be = grid[1:][sign[1:] != sign[:-1]]
        # Monte Carlo under our distribution
        u = rng.random(MC_PATHS)
        z = np.abs(rng.standard_normal(MC_PATHS))
        J = np.where(u < f.p_up, z * f.m_up / 100 * SQ, -z * f.m_down / 100 * SQ)
        V_d = diag["skew_math"][tk]["V_d"]
        ST = S * np.exp(J + rng.standard_normal(MC_PATHS) * np.sqrt(V_d) - V_d / 2)
        sim = pnl(ST)
        edge = 0.5 * abs(f.skew_gap_vols) * np.mean(vega_abs) * 2 if f.skew_flag and not f.vol_flag else np.nan
        rows.append(dict(
            ticker=tk, structure=spec["name"], expiry=im.post_expiry, spot=S,
            legs=" | ".join(legs), net_premium=round(prem, 2),
            premium_type="credit" if prem > 0 else "debit",
            stock_hedge_shares=round(stock, 3), package_delta=round(delta, 3),
            max_gain=round(pg.max(), 2), max_loss=round(-pg.min(), 2),
            max_loss_up=round(-pnl([1.8 * S])[0], 2) if not spec.get("hedge_delta") else np.nan,
            breakevens=" / ".join(f"{x:.1f}" for x in be),
            breakevens_pct=" / ".join(f"{(x / S - 1) * 100:+.1f}%" for x in be),
            pnl_at_minus25=round(pnl([0.75 * S])[0], 2), pnl_at_plus25=round(pnl([1.25 * S])[0], 2),
            scenarios=" | ".join(f"{m:+d}%: {pnl([S * (1 + m / 100)])[0]:+.2f}" for m in (-40, -25, -15, -10, -5, 0, 5, 10, 15, 25, 40)),
            worst_pnl_within_40pct=round(pnl(np.linspace(0.6 * S, 1.4 * S, 8001)).min(), 2),
            worst_at=round(np.linspace(0.6 * S, 1.4 * S, 8001)[pnl(np.linspace(0.6 * S, 1.4 * S, 8001)).argmin()], 1),
            model_win_rate=round((sim > 0).mean(), 3),
            model_ev=round(sim.mean(), 2), model_ev_pct_spot=round(sim.mean() / S * 100, 2),
            skew_edge_per_share=round(edge, 2) if np.isfinite(edge) else np.nan,
        ))
    out = pd.DataFrame(rows)
    out.to_csv(DATA / "trade_ideas.csv", index=False)
    pd.set_option("display.width", 250, "display.max_colwidth", 200)
    print(out.T.to_string())


if __name__ == "__main__":
    main()
