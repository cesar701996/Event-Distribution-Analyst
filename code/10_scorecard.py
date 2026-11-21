"""
Step 10 - Score past forecasts against what actually happened.

Run it any time after an event has reported:

    python code/10_scorecard.py                     # score everything that has reported by today
    python code/10_scorecard.py --as-of 2026-12-19  # pretend "today" is a given date
    python code/10_scorecard.py --prices my.csv     # use your own closes (ticker,date,close) if yfinance fails

What it does:
1. Freezes each forecast in forecast_log.csv (plus the priced trade from data/trade_ideas.csv)
   into data/scorecard.csv. A forecast is frozen the first time it is seen and never rewritten,
   so later runs of the pipeline cannot change what we said before the event.
2. For every event whose reaction day has passed, pulls closing prices and fills in the actual
   move, how it compared with our forecast and with the options-implied move, and whether the
   direction call was right.
3. For every trade idea whose option expiry has passed, values the structure at expiry from the
   legs and marks we priced it at (static stock hedge held to expiry, as in the report's scenario
   grid) and records the P&L.
4. Writes scorecard.md, copies the results into forecast_log.csv, and prints a summary.

Honest limits: the trades are valued at the Friday-close mid marks used in the report, not real
fills; a delta-hedged trade is scored with its starting hedge only; and a handful of events is far
too few to say whether the method has an edge. The point is to keep score, misses included.
"""
import argparse
import datetime as dt
import re

import numpy as np
import pandas as pd

from config import ROOT, DATA
from utils import next_trading_day, HOL

SCORE = DATA / "scorecard.csv"
LOG = ROOT / "forecast_log.csv"

FROZEN = ["ticker", "event_date", "event_timing", "reaction_date", "spot_asof",
          "implied_move_straddle_pct", "implied_event_move_pct", "forecast_move_pct", "gap_pct_of_implied",
          "direction_call", "confidence", "p_up", "skew_view", "market_rr25", "model_rr25",
          "trade_idea", "trade_structure", "trade_expiry", "trade_spot", "trade_legs", "trade_net_premium",
          "trade_stock_hedge_shares", "trade_breakevens"]
RESULTS = ["pre_close", "post_close", "actual_move_pct", "actual_abs_move_pct", "abs_move_vs_implied",
           "abs_move_vs_forecast", "closer_to_actual", "direction_correct",
           "expiry_close", "trade_pnl_per_share", "trade_pnl_pct_spot", "trade_result", "scored_on"]

LEG = re.compile(r"([+-]?\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)([CP])\s*@\s*(\d+(?:\.\d+)?)")


# ---------------------------------------------------------------- freezing forecasts
def freeze():
    log = pd.read_csv(LOG, dtype=str).fillna("")
    ti = pd.read_csv(DATA / "trade_ideas.csv", dtype=str).set_index("ticker") if (DATA / "trade_ideas.csv").exists() else None
    old = pd.read_csv(SCORE, dtype=str).fillna("") if SCORE.exists() else pd.DataFrame(columns=FROZEN + RESULTS)
    seen = set(zip(old.ticker, old.event_date))
    new = []
    for _, r in log.iterrows():
        if (r.ticker, r.event_date) in seen:
            continue
        row = {c: r.get(c, "") for c in FROZEN}
        if r.trade_idea == "yes" and ti is not None and r.ticker in ti.index:
            t = ti.loc[r.ticker]
            row.update(trade_expiry=t.expiry, trade_spot=t.spot, trade_legs=t.legs, trade_net_premium=t.net_premium,
                       trade_stock_hedge_shares=t.stock_hedge_shares, trade_breakevens=t.breakevens)
        row.update({c: "" for c in RESULTS})
        new.append(row)
    if new:
        print(f"Froze {len(new)} new forecast(s): " + ", ".join(f"{x['ticker']} {x['event_date']}" for x in new))
    return pd.concat([old, pd.DataFrame(new)], ignore_index=True)[FROZEN + RESULTS] if new else old[FROZEN + RESULTS]


# ---------------------------------------------------------------- prices
class Prices:
    def __init__(self, manual=None):
        self.manual = pd.read_csv(manual, dtype={"date": str}) if manual else None
        self.cache = {}

    def close(self, ticker, date):
        date = str(date)
        if self.manual is not None:
            hit = self.manual[(self.manual.ticker == ticker) & (self.manual.date == date)]
            if len(hit):
                return float(hit.close.iloc[0])
        if ticker not in self.cache:
            import yfinance as yf
            h = yf.Ticker(ticker).history(period="6mo", auto_adjust=False)["Close"].dropna()
            h.index = [str(i.date()) for i in h.index]
            self.cache[ticker] = h
        h = self.cache[ticker]
        return float(h[date]) if date in h.index else np.nan


def prev_trading_day(d):
    return np.busday_offset(np.datetime64(str(d)), -1, roll="backward", holidays=HOL).astype(object)


# ---------------------------------------------------------------- trade valuation
def trade_value_at_expiry(legs, spot, hedge, s_t):
    """P&L per share: each leg's intrinsic value at expiry minus what we paid (or plus what we
    received), plus a static stock hedge marked from `spot` to `s_t`."""
    pnl = 0.0
    parsed = LEG.findall(legs)
    if not parsed:
        raise ValueError(f"could not read legs: {legs}")
    for qty, strike, kind, px in parsed:
        qty, strike, px = float(qty), float(strike), float(px)
        intrinsic = max(0.0, s_t - strike) if kind == "C" else max(0.0, strike - s_t)
        pnl += qty * (intrinsic - px)
    return pnl + (float(hedge) if hedge not in ("", None) else 0.0) * (s_t - float(spot))


# ---------------------------------------------------------------- scoring
def score(df, prices, today):
    for i, r in df.iterrows():
        if r.actual_move_pct == "" and r.reaction_date and pd.Timestamp(r.reaction_date).date() <= today:
            pre_day = r.event_date if r.event_timing.upper() == "AMC" else prev_trading_day(r.event_date)
            pre, post = prices.close(r.ticker, pre_day), prices.close(r.ticker, r.reaction_date)
            if np.isfinite(pre) and np.isfinite(post):
                mv = (post / pre - 1) * 100
                a, fc, im = abs(mv), float(r.forecast_move_pct), float(r.implied_event_move_pct)
                call = r.direction_call.lower()
                df.loc[i, ["pre_close", "post_close", "actual_move_pct", "actual_abs_move_pct",
                           "abs_move_vs_implied", "abs_move_vs_forecast", "closer_to_actual", "direction_correct",
                           "scored_on"]] = [
                    f"{pre:.2f}", f"{post:.2f}", f"{mv:+.2f}", f"{a:.2f}", f"{a / im:.2f}", f"{a / fc:.2f}",
                    "our forecast" if abs(a - fc) < abs(a - im) else ("tie" if abs(a - fc) == abs(a - im) else "options market"),
                    "n/a (neutral call)" if call not in ("up", "down") else ("yes" if (mv > 0) == (call == "up") else "no"),
                    str(today)]
            else:
                print(f"  {r.ticker}: no close for {pre_day} or {r.reaction_date} yet; skipped")
        if r.trade_idea == "yes" and r.trade_result == "" and r.trade_expiry and pd.Timestamp(r.trade_expiry).date() <= today:
            s_t = prices.close(r.ticker, r.trade_expiry)
            if np.isfinite(s_t):
                pnl = trade_value_at_expiry(r.trade_legs, r.trade_spot, r.trade_stock_hedge_shares, s_t)
                df.loc[i, ["expiry_close", "trade_pnl_per_share", "trade_pnl_pct_spot", "trade_result"]] = [
                    f"{s_t:.2f}", f"{pnl:+.2f}", f"{pnl / float(r.trade_spot) * 100:+.2f}",
                    "win" if pnl > 0 else ("flat" if pnl == 0 else "loss")]
            else:
                print(f"  {r.ticker}: no close for expiry {r.trade_expiry} yet; trade not scored")
    return df


# ---------------------------------------------------------------- outputs
def write_markdown(df, today):
    done = df[df.actual_move_pct != ""]
    L = [f"# Forecast scorecard (as of {today})", "",
         "Every forecast is frozen before its event in `data/scorecard.csv` and scored after it. "
         "Misses stay in. Trades are valued at the report's mid marks with the starting hedge held to expiry, "
         "so real fills would differ.", ""]
    L += ["| Ticker | Event | Implied move | Our forecast | Actual move | Closer | Direction call | Right? | Trade | Trade P&L / share |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in df.sort_values("event_date").iterrows():
        actual = f"{r.actual_move_pct}%" if r.actual_move_pct else "pending"
        trade = "none" if r.trade_idea != "yes" else (r.trade_result or "pending")
        pnl = f"{r.trade_pnl_per_share} ({r.trade_pnl_pct_spot}% of spot)" if r.trade_pnl_per_share else ""
        L.append(f"| {r.ticker} | {r.event_date} | {float(r.implied_event_move_pct):.1f}% | {float(r.forecast_move_pct):.1f}% | "
                 f"{actual} | {r.closer_to_actual or ''} | {r.direction_call} ({r.confidence}/5) | {r.direction_correct or ''} | {trade} | {pnl} |")
    L.append("")
    if len(done):
        a = done.actual_abs_move_pct.astype(float)
        err_ours = (a - done.forecast_move_pct.astype(float)).abs().mean()
        err_mkt = (a - done.implied_event_move_pct.astype(float)).abs().mean()
        ours = (done.closer_to_actual == "our forecast").sum()
        dirs = done[done.direction_correct.isin(["yes", "no"])]
        trades = df[df.trade_result.isin(["win", "loss", "flat"])]
        L += ["## Summary", "",
              f"- Events scored: {len(done)}",
              f"- Average miss on move size: ours {err_ours:.2f} pts vs. options market {err_mkt:.2f} pts",
              f"- Our forecast was closer on {ours} of {len(done)}",
              f"- Direction calls (non-neutral): {(dirs.direction_correct == 'yes').sum()} of {len(dirs)} right",
              f"- Trades scored: {len(trades)}, wins {(trades.trade_result == 'win').sum()}, "
              f"average P&L {trades.trade_pnl_pct_spot.astype(float).mean():+.2f}% of spot"
              if len(trades) else "- Trades scored: 0 so far",
              "", f"With {len(done)} events this is a record, not evidence of an edge.", ""]
    (ROOT / "scorecard.md").write_text("\n".join(L))


def sync_log(df):
    """Copy results into forecast_log.csv for the rows it still lists."""
    if not LOG.exists():
        return
    log = pd.read_csv(LOG, dtype=str).fillna("")
    s = df.set_index(["ticker", "event_date"])
    for i, r in log.iterrows():
        k = (r.ticker, r.event_date)
        if k not in s.index:
            continue
        x = s.loc[k]
        for c in ["actual_move_pct", "actual_abs_move_pct", "abs_move_vs_implied", "abs_move_vs_forecast", "direction_correct"]:
            log.loc[i, c] = x[c]
        if x.trade_result:
            log.loc[i, "trade_result"] = f"{x.trade_result} {x.trade_pnl_per_share}/sh at expiry ({x.expiry_close})"
    log.to_csv(LOG, index=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=None, help="score as if today were this date (YYYY-MM-DD)")
    ap.add_argument("--prices", default=None, help="optional CSV of closes: ticker,date,close")
    a = ap.parse_args()
    today = dt.date.fromisoformat(a.as_of) if a.as_of else dt.date.today()
    df = score(freeze(), Prices(a.prices), today)
    df.to_csv(SCORE, index=False)
    write_markdown(df, today)
    sync_log(df)
    done = (df.actual_move_pct != "").sum()
    print(f"Scorecard: {done} of {len(df)} events scored, "
          f"{df.trade_result.isin(['win', 'loss', 'flat']).sum()} trade(s) settled. See scorecard.md.")


if __name__ == "__main__":
    main()
