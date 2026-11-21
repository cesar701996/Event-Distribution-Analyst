"""
Step 1 - Calendar.

For every name in the universe:
  * pull earnings dates (past and next) from yfinance
  * pull the list of 8-K filings tagged Item 2.02 ("Results of Operations") from
    SEC EDGAR, which we use later to sanity-check yfinance's historical dates
  * mark whether the next event falls inside the look-ahead window

Outputs
  data/earnings_dates_yf.csv   all yfinance earnings rows (past + upcoming)
  data/edgar_8k_202.csv        8-K Item 2.02 filing dates per ticker
  data/calendar.csv            one row per ticker: next event, timing, in-window flag
"""
import warnings
import pandas as pd
import yfinance as yf

from config import UNIVERSE, AS_OF, WINDOW_END, DATA
from utils import sec_get

warnings.filterwarnings("ignore")


def yf_earnings(ticker):
    t = yf.Ticker(ticker)
    df = t.get_earnings_dates(limit=24).reset_index()
    df.columns = ["datetime", "eps_est", "eps_reported", "surprise_pct"]
    df["ticker"] = ticker
    # Hour of the timestamp tells us before-open vs after-close.
    # AMC = after market close, BMO = before market open, DMH = during market hours
    df["timing"] = df["datetime"].apply(lambda x: "AMC" if x.hour >= 16 else ("BMO" if x.hour < 12 else "DMH"))
    df["date"] = df["datetime"].dt.date
    info_flag = None
    try:
        info_flag = t.info.get("isEarningsDateEstimate")
    except Exception:
        pass
    return df, info_flag


def edgar_cik_map():
    j = sec_get("https://www.sec.gov/files/company_tickers.json").json()
    return {v["ticker"]: str(v["cik_str"]).zfill(10) for v in j.values()}


def edgar_202_dates(cik):
    """All 8-K filings with Item 2.02 from the EDGAR submissions API (follows paging files)."""
    sub = sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json").json()
    frames = [pd.DataFrame(sub["filings"]["recent"])]
    for f in sub["filings"].get("files", []):
        frames.append(pd.DataFrame(sec_get(f"https://data.sec.gov/submissions/{f['name']}").json()))
    df = pd.concat(frames, ignore_index=True)
    df = df[(df["form"] == "8-K") & df["items"].fillna("").str.contains("2.02")]
    return df[["filingDate", "acceptanceDateTime", "accessionNumber", "primaryDocument", "items"]]


def main():
    rows, cal = [], []
    for tk in UNIVERSE:
        try:
            df, est_flag = yf_earnings(tk)
            rows.append(df)
        except Exception as e:
            print(f"[{tk}] yfinance earnings dates FAILED: {e}")
            cal.append(dict(ticker=tk, next_event=None, status="data error"))
            continue
        fut = df[df["date"] >= AS_OF].sort_values("date")
        nxt = fut.iloc[0] if len(fut) else None
        last = df[df["date"] < AS_OF].sort_values("date").iloc[-1]
        in_win = nxt is not None and AS_OF <= nxt["date"] <= WINDOW_END
        cal.append(dict(
            ticker=tk,
            last_event=last["date"],
            next_event=None if nxt is None else nxt["date"],
            timing=None if nxt is None else nxt["timing"],
            date_is_estimate=est_flag,
            in_window=in_win,
            status="EVENT" if in_win else "no catalyst",
        ))
    allrows = pd.concat(rows, ignore_index=True)
    allrows.to_csv(DATA / "earnings_dates_yf.csv", index=False)

    # EDGAR cross-check list (failure here is not fatal; base rates fall back to yfinance only)
    try:
        ciks = edgar_cik_map()
        ed = []
        for tk in UNIVERSE:
            d = edgar_202_dates(ciks[tk])
            d.insert(0, "ticker", tk)
            ed.append(d)
        pd.concat(ed).to_csv(DATA / "edgar_8k_202.csv", index=False)
        print("EDGAR 8-K Item 2.02 list saved.")
    except Exception as e:
        print(f"EDGAR pull FAILED ({e}); historical dates will not be cross-checked.")

    cal = pd.DataFrame(cal)
    cal.to_csv(DATA / "calendar.csv", index=False)
    print(f"Window: {AS_OF} to {WINDOW_END}")
    print(cal.to_string(index=False))


if __name__ == "__main__":
    main()
