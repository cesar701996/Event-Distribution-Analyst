"""
Step 4 - Fundamental inputs (data pull only; the judgment lives in analyst_views.json).

For each name with an event in the window:
  * yfinance consensus for the quarter being reported (EPS and revenue),
    EPS trend (consensus now vs 7/30/60/90 days ago) and revision counts,
    last 4 EPS surprises, analyst price targets
  * SEC EDGAR: the latest earnings press release (8-K Item 2.02, exhibit 99.1).
    We save the full text and pull out sentences that mention guidance or
    outlook, so the analyst can compare company guidance with consensus.
  * recent headlines from yfinance news (for sector news since the last report)

Transcripts: company IR sites host webcasts, not free text transcripts, so the
press release (which carries management quotes and guidance) is the text source.

Outputs (data/fundamentals/)
  <TK>_consensus.json, <TK>_ex991.txt, <TK>_guidance_snippets.txt, <TK>_news.csv
  fundamentals_summary.csv
"""
import json
import re
import html
import warnings
import pandas as pd
import yfinance as yf
from bs4 import BeautifulSoup

from config import AS_OF, DATA
from utils import sec_get

warnings.filterwarnings("ignore")
OUT = DATA / "fundamentals"
OUT.mkdir(exist_ok=True)

GUIDE_PAT = re.compile(
    r"(guidance|outlook|expects?|expected|targets?|anticipat|forecast|project(?:s|ed)?|"
    r"remaining performance obligation|RPO|backlog|next quarter|fourth quarter|third quarter|"
    r"fiscal (?:year )?20\d\d)", re.I)


def df_json(x):
    if x is None or (hasattr(x, "empty") and x.empty):
        return None
    return json.loads(x.to_json(date_format="iso"))


def consensus(tk):
    t = yf.Ticker(tk)
    out = {}
    for name in ["earnings_estimate", "revenue_estimate", "eps_trend", "eps_revisions",
                 "growth_estimates", "earnings_history"]:
        try:
            out[name] = df_json(getattr(t, name))
        except Exception as e:
            out[name] = f"FAILED: {e}"
    try:
        out["analyst_price_targets"] = t.analyst_price_targets
    except Exception as e:
        out["analyst_price_targets"] = f"FAILED: {e}"
    try:
        i = t.info
        out["info"] = {k: i.get(k) for k in ["regularMarketPrice", "forwardPE", "trailingPE",
                                              "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "marketCap",
                                              "recommendationMean", "numberOfAnalystOpinions",
                                              "shortPercentOfFloat", "beta"]}
    except Exception as e:
        out["info"] = f"FAILED: {e}"
    return out


def latest_ex991(tk):
    """Find the newest Item 2.02 8-K before AS_OF and return (date, url, text) of its EX-99.1."""
    e = pd.read_csv(DATA / "edgar_8k_202.csv", parse_dates=["filingDate"])
    e = e[(e.ticker == tk) & (e.filingDate.dt.date < AS_OF)].sort_values("filingDate")
    row = e.iloc[-1]
    ciks = sec_get("https://www.sec.gov/files/company_tickers.json").json()
    cik = next(int(v["cik_str"]) for v in ciks.values() if v["ticker"] == tk)
    acc = row.accessionNumber.replace("-", "")
    idx_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{row.accessionNumber}-index.htm"
    idx = sec_get(idx_url).text
    doc = None
    for tr in re.split(r"<tr", idx, flags=re.I):
        if re.search(r">\s*EX-99\.?1\s*<", tr, re.I):
            m = re.search(r'href="([^"]+)"', tr)
            if m:
                doc = "https://www.sec.gov" + m.group(1)
                break
    if doc is None:
        raise RuntimeError(f"no EX-99.1 found in {idx_url}")
    soup = BeautifulSoup(sec_get(doc).text, "html.parser")
    text = html.unescape(soup.get_text(" "))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return row.filingDate.date(), doc, text


def guidance_snippets(text, max_n=40):
    sents = re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", text.replace("\n", " "))
    hits = [s.strip() for s in sents if GUIDE_PAT.search(s) and 40 < len(s) < 900]
    return hits[:max_n]


def news(tk, n=25):
    rows = []
    for it in yf.Ticker(tk).get_news(count=n):
        c = it.get("content", it)
        prov = c.get("provider")
        rows.append(dict(date=c.get("pubDate"), source=prov.get("displayName") if isinstance(prov, dict) else None,
                         title=c.get("title"), summary=(c.get("summary") or "")[:400]))
    return pd.DataFrame(rows)


def main():
    cal = pd.read_csv(DATA / "calendar.csv")
    summary = []
    for tk in cal[cal.in_window].ticker:
        rec = dict(ticker=tk)
        cons = consensus(tk)
        (OUT / f"{tk}_consensus.json").write_text(json.dumps(cons, indent=1, default=str))
        try:
            ee, re_ = cons["earnings_estimate"], cons["revenue_estimate"]
            et, rv = cons["eps_trend"], cons["eps_revisions"]
            rec.update(eps_cons_0q=ee["avg"]["0q"], eps_low=ee["low"]["0q"], eps_high=ee["high"]["0q"],
                       n_analysts=ee["numberOfAnalysts"]["0q"], eps_yago=ee["yearAgoEps"]["0q"],
                       rev_cons_0q=re_["avg"]["0q"], rev_growth_0q=re_["growth"]["0q"],
                       eps_now=et["current"]["0q"], eps_30d_ago=et["30daysAgo"]["0q"],
                       eps_90d_ago=et["90daysAgo"]["0q"],
                       eps_rev_30d_pct=(et["current"]["0q"] / et["30daysAgo"]["0q"] - 1) * 100,
                       eps_rev_90d_pct=(et["current"]["0q"] / et["90daysAgo"]["0q"] - 1) * 100,
                       fy_eps_rev_90d_pct=(et["current"]["0y"] / et["90daysAgo"]["0y"] - 1) * 100,
                       up_30d=rv["upLast30days"]["0q"], down_30d=rv["downLast30days"]["0q"])
        except Exception as e:
            print(f"[{tk}] consensus parse failed: {e}")
        try:
            d, url, text = latest_ex991(tk)
            (OUT / f"{tk}_ex991.txt").write_text(f"SOURCE: {url}\nFILED: {d}\n\n{text}")
            snips = guidance_snippets(text)
            (OUT / f"{tk}_guidance_snippets.txt").write_text(
                f"SOURCE: {url}\nFILED: {d}\n\n" + "\n\n".join(f"- {s}" for s in snips))
            rec.update(last_release=d, release_url=url, n_guidance_snippets=len(snips))
        except Exception as e:
            print(f"[{tk}] EDGAR press release FAILED: {e}")
            rec.update(last_release=None, release_url=f"FAILED: {e}")
        try:
            news(tk).to_csv(OUT / f"{tk}_news.csv", index=False)
        except Exception as e:
            print(f"[{tk}] news FAILED: {e}")
        summary.append(rec)
    s = pd.DataFrame(summary)
    s.to_csv(OUT / "fundamentals_summary.csv", index=False)
    print(s.T.to_string())


if __name__ == "__main__":
    main()
