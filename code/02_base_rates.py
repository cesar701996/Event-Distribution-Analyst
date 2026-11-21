"""
Step 2 - Base rates.

For each ticker, take the last 12 reported earnings and measure the 1-day
reaction:
  * after-close (AMC) report:  close(next trading day) / close(report day) - 1
  * before-open (BMO) report:  close(report day) / close(prior trading day) - 1
Prices are split/dividend adjusted (yfinance auto_adjust=True).

Each yfinance date is cross-checked against SEC EDGAR 8-K Item 2.02 filings
(the press-release 8-K). A date with no 8-K within +/-1 day is flagged.

We also store the realized vol of the 20 sessions before each report, which the
forecast step uses to scale history to today's volatility regime.

Run for all 12 names (the alt-data backtest uses the extra history), but the
report only shows names with an event in the window.

Outputs
  data/earnings_moves.csv   one row per (ticker, event)
  data/base_rates.csv       summary stats per ticker
"""
import warnings
import numpy as np
import pandas as pd
import yfinance as yf

from config import UNIVERSE, AS_OF, N_EVENTS, DATA

warnings.filterwarnings("ignore")


def load_prices():
    px = yf.download(UNIVERSE, start="2022-01-01", end=str(AS_OF + pd.Timedelta(days=1)),
                     auto_adjust=True, progress=False)["Close"]
    px.index = px.index.tz_localize(None).normalize()
    return px


def reaction(close, d, timing):
    """Return (pre_close_date, post_close_date, move) for an event on date d."""
    d = pd.Timestamp(d)
    idx = close.index
    if timing == "BMO":
        post = idx[idx >= d][0]
        pre = idx[idx < post][-1]
    else:  # AMC (or unknown -> treat as AMC, all 12 names report after the close)
        pre = idx[idx <= d][-1]
        post = idx[idx > pre][0]
    return pre, post, close[post] / close[pre] - 1


def main():
    px = load_prices()
    ed = pd.read_csv(DATA / "earnings_dates_yf.csv", parse_dates=["datetime"])
    ed["date"] = pd.to_datetime(ed["date"])
    try:
        edgar = pd.read_csv(DATA / "edgar_8k_202.csv", parse_dates=["filingDate"])
    except FileNotFoundError:
        edgar = pd.DataFrame(columns=["ticker", "filingDate", "acceptanceDateTime"])

    logret = np.log(px).diff()
    rows = []
    for tk in UNIVERSE:
        e8k = edgar[edgar.ticker == tk]
        # A past date counts as a reported event if yfinance has a reported EPS OR EDGAR
        # shows an Item 2.02 8-K within a day (yfinance sometimes leaves the latest EPS blank).
        past = ed[(ed.ticker == tk) & (ed.date.dt.date < AS_OF)].copy()
        has_8k = past.date.apply(lambda d: bool(((e8k.filingDate - d).dt.days.abs() <= 1).any()))
        past = past[past.eps_reported.notna() | has_8k]
        past = past.sort_values("date").tail(N_EVENTS)
        close = px[tk].dropna()
        for _, r in past.iterrows():
            pre, post, mv = reaction(close, r.date, r.timing)
            # EDGAR cross-check: an Item 2.02 8-K filed within one day of the yfinance date
            gap = (e8k.filingDate - r.date).dt.days.abs()
            match = e8k[gap <= 1]
            # realized vol (annualized) of the 20 sessions ending at the pre-event close
            rv = logret[tk].loc[:pre].tail(20).std() * np.sqrt(252)
            rows.append(dict(
                ticker=tk, event_date=r.date.date(), timing=r.timing,
                pre_close_date=pre.date(), post_close_date=post.date(),
                pre_close=round(close[pre], 4), post_close=round(close[post], 4),
                move_pct=round(mv * 100, 3), abs_move_pct=round(abs(mv) * 100, 3),
                eps_est=r.eps_est, eps_reported=r.eps_reported, surprise_pct=r.surprise_pct,
                rv20_pre=round(rv * 100, 2),
                edgar_8k_match=bool(len(match)),
                edgar_accept_time=(match.acceptanceDateTime.iloc[0] if len(match) else None),
            ))
    mv = pd.DataFrame(rows)
    mv.to_csv(DATA / "earnings_moves.csv", index=False)

    # ---- summary statistics per ticker
    out = []
    for tk, g in mv.groupby("ticker", sort=False):
        m = g.move_pct
        a = g.abs_move_pct
        out.append(dict(
            ticker=tk, n=len(g),
            first_event=g.event_date.min(), last_event=g.event_date.max(),
            mean_abs=a.mean(), median_abs=a.median(), std_abs=a.std(),
            se_mean_abs=a.std() / np.sqrt(len(a)),
            mean_abs_last4=a.tail(4).mean(),
            mean_signed=m.mean(), median_signed=m.median(), std_signed=m.std(),
            max_up=m.max(), max_down=m.min(),
            n_up=int((m > 0).sum()), n_down=int((m < 0).sum()),
            share_up=(m > 0).mean(),
            mean_up_move=m[m > 0].mean() if (m > 0).any() else np.nan,
            mean_down_move=-m[m < 0].mean() if (m < 0).any() else np.nan,
            skewness=m.skew(),
            median_rv20_pre=g.rv20_pre.median(),
            edgar_checked=int(g.edgar_8k_match.sum()),
        ))
    br = pd.DataFrame(out).round(3)
    br.to_csv(DATA / "base_rates.csv", index=False)
    print(br[["ticker", "n", "mean_abs", "median_abs", "std_signed", "max_up", "max_down",
              "share_up", "mean_up_move", "mean_down_move", "median_rv20_pre", "edgar_checked"]].to_string(index=False))
    bad = mv[~mv.edgar_8k_match]
    if len(bad):
        print("\nDates with no matching EDGAR 8-K (check manually):")
        print(bad[["ticker", "event_date"]].to_string(index=False))


if __name__ == "__main__":
    main()
