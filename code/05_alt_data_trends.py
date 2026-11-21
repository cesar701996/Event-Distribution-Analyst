"""
Step 5 - Alternative data: Google Trends search attention before earnings.

Idea: investor attention ahead of a report tends to go with a bigger immediate
price reaction (Da, Engelberg & Gao 2011 use Google search volume as an
attention measure). We build an Abnormal Search Volume Index (ASVI) per name:

    ASVI(A) = ln( mean SVI of the last 2 complete weeks before date A )
            - ln( median SVI of the 8 weeks before those )

where SVI is weekly Google Trends interest for the company *topic* (not the raw
word, so "Oracle" the company is not mixed up with the word "oracle").

Score for an upcoming event:
    A = AS_OF (so the lead time L = event date - AS_OF)
    z = (ASVI now - mean of ASVI at the same lead L before the last 12 reports)
        / std of those 12 values
    score = clip(z, -2, 2)

Backtest (all 12 names, 144 events, lead 14 days): regress the relative
earnings move (|move| / name's mean |move|) on the within-name z-score. The
pooled slope b sets the size multiplier used in the forecast:
    alt multiplier = clip(1 + 0.5 * b * score, 0.90, 1.10)     (half the fitted slope = shrinkage)

Weaknesses are listed in the report. Requests are cached in data/alt/ so a
rerun does not hit Google again (delete the cache to refresh).

Outputs
  data/alt/trends_<TK>.csv, data/alt/trends_topics.json
  data/alt/asvi_backtest.csv, data/alt/alt_scores.csv, data/alt/backtest_stats.json
"""
import json
import time
import warnings
import numpy as np
import pandas as pd
from scipy import stats
from pytrends.request import TrendReq

from config import UNIVERSE, AS_OF, DATA, ALT_CAP

warnings.filterwarnings("ignore")
ALT = DATA / "alt"
ALT.mkdir(exist_ok=True)

COMPANY_NAMES = {"AAPL": "Apple Inc.", "MSFT": "Microsoft", "NVDA": "Nvidia", "GOOGL": "Google",
                 "AMZN": "Amazon", "META": "Meta Platforms", "AVGO": "Broadcom", "ORCL": "Oracle Corporation",
                 "CRM": "Salesforce", "ADBE": "Adobe Inc.", "AMD": "AMD", "NFLX": "Netflix"}
# Google's autocomplete (used to find topic ids) is flaky, so known Knowledge Graph
# ids are pinned here. Anything not listed is looked up via pytrends.suggestions().
PINNED_TOPICS = {"MSFT": "/m/04sv4",     # Microsoft Corporation
                 "AMZN": "/m/0mgkg",     # Amazon.com
                 "GOOGL": "/m/045c7b",   # Google
                 "NFLX": "/m/017rf_"}    # Netflix
BACKTEST_LEAD = 14
# Google returns WEEKLY data only for windows under 5 years; 4 years covers the 12 events + baseline.
TIMEFRAME_START = "2022-11-27"


def pytrends_client():
    return TrendReq(hl="en-US", tz=0, timeout=(10, 30))


def with_retry(fn, tries=4, wait=20):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            if i == tries - 1:
                raise
            print(f"   retry {i + 1} after error: {type(e).__name__} {str(e)[:80]}")
            time.sleep(wait * (i + 1))


def topic_mid(pt, tk):
    """Find the Google Knowledge Graph topic id for the company (falls back to the search term)."""
    sug = with_retry(lambda: pt.suggestions(COMPANY_NAMES[tk]))
    for s in sug:
        typ = s.get("type", "").lower()
        if any(w in typ for w in ["company", "corporation", "technology", "software", "manufacturer", "enterprise"]):
            return s["mid"], f'{s["title"]} ({s["type"]})'
    return COMPANY_NAMES[tk], "search term (no company topic found)"


def get_series(pt, tk, topics):
    f = ALT / f"trends_{tk}.csv"
    if f.exists():
        return pd.read_csv(f, parse_dates=["date"])
    if tk not in topics and tk in PINNED_TOPICS:
        topics[tk] = {"mid": PINNED_TOPICS[tk], "label": f"{COMPANY_NAMES[tk]} (pinned topic id)"}
    if tk not in topics:
        mid, label = topic_mid(pt, tk)
        topics[tk] = {"mid": mid, "label": label}
        time.sleep(6)
    kw = topics[tk]["mid"]

    def pull():
        pt.build_payload([kw], timeframe=f"{TIMEFRAME_START} {AS_OF + pd.Timedelta(days=1)}")
        return pt.interest_over_time()
    df = with_retry(pull).reset_index().rename(columns={kw: "svi"})
    if df.empty:
        raise RuntimeError(f"empty Trends series for {kw}")
    df = df[["date", "svi", "isPartial"]]
    df.to_csv(f, index=False)
    time.sleep(10)
    return df


def asvi(s, asof):
    """s: weekly series (date = week start, Sunday). Uses only weeks fully completed by `asof`."""
    done = s[s.date + pd.Timedelta(days=6) <= pd.Timestamp(asof)].tail(10)
    if len(done) < 10:
        return np.nan
    recent = done.svi.iloc[-2:].mean() + 1
    base = done.svi.iloc[:8].median() + 1
    return np.log(recent) - np.log(base)


def main():
    topics_f = ALT / "trends_topics.json"
    topics = json.loads(topics_f.read_text()) if topics_f.exists() else {}
    pt = pytrends_client()
    moves = pd.read_csv(DATA / "earnings_moves.csv", parse_dates=["event_date"])
    br = pd.read_csv(DATA / "base_rates.csv").set_index("ticker")
    cal = pd.read_csv(DATA / "calendar.csv", parse_dates=["next_event"])

    series, failed = {}, []
    for tk in UNIVERSE:
        try:
            series[tk] = get_series(pt, tk, topics)
            print(f"{tk}: {len(series[tk])} weeks  topic={topics.get(tk, {}).get('label')}")
        except Exception as e:
            print(f"{tk}: Google Trends FAILED ({e}); excluded from signal")
            failed.append(tk)
        topics_f.write_text(json.dumps(topics, indent=1))

    # ---- backtest: does pre-earnings attention line up with bigger moves?
    bt = []
    for tk, s in series.items():
        g = moves[moves.ticker == tk]
        for _, r in g.iterrows():
            bt.append(dict(ticker=tk, event_date=r.event_date.date(),
                           asvi=asvi(s, r.event_date - pd.Timedelta(days=BACKTEST_LEAD)),
                           rel_move=r.abs_move_pct / br.loc[tk, "mean_abs"],
                           rv_ratio=r.rv20_pre / br.loc[tk, "median_rv20_pre"]))
    bt = pd.DataFrame(bt).dropna()
    bt["z"] = bt.groupby("ticker").asvi.transform(lambda x: (x - x.mean()) / x.std())
    bt.to_csv(ALT / "asvi_backtest.csv", index=False)
    ols = stats.linregress(bt.z, bt.rel_move)
    st = dict(n=len(bt), names=len(series),
              pearson=stats.pearsonr(bt.z, bt.rel_move)[0],
              spearman=stats.spearmanr(bt.z, bt.rel_move)[0],
              slope=ols.slope, slope_t=ols.slope / ols.stderr, slope_p=ols.pvalue,
              corr_with_rv_ratio=stats.spearmanr(bt.z, bt.rv_ratio)[0],
              top_tercile_rel_move=bt[bt.z > bt.z.quantile(2 / 3)].rel_move.mean(),
              bottom_tercile_rel_move=bt[bt.z < bt.z.quantile(1 / 3)].rel_move.mean(),
              failed=failed)
    (ALT / "backtest_stats.json").write_text(json.dumps(st, indent=1, default=float))
    print("\nBacktest:", json.dumps(st, indent=1, default=float))

    # ---- scores for names with an event in the window
    out = []
    for _, e in cal[cal.in_window].iterrows():
        tk = e.ticker
        if tk not in series:
            out.append(dict(ticker=tk, score=0.0, alt_mult=1.0, note="Trends unavailable -> neutral"))
            continue
        s = series[tk]
        lead = (e.next_event.date() - AS_OF).days
        hist = [asvi(s, d - pd.Timedelta(days=lead)) for d in moves[moves.ticker == tk].event_date]
        hist = np.array([h for h in hist if np.isfinite(h)])
        now = asvi(s, AS_OF)
        # sensitivity: include the current, still-partial week (the selloff week of Nov 15-21)
        now_incl_partial = np.log(s.svi.iloc[-2:].mean() + 1) - np.log(s.svi.iloc[-10:-2].median() + 1)
        z = (now - hist.mean()) / hist.std(ddof=1)
        score = float(np.clip(z, -2, 2))
        mult = float(np.clip(1 + 0.5 * st["slope"] * score, *ALT_CAP))
        out.append(dict(ticker=tk, lead_days=lead, asvi_now=round(now, 3),
                        asvi_hist_mean=round(hist.mean(), 3), asvi_hist_std=round(hist.std(ddof=1), 3),
                        z=round(z, 2), score=round(score, 2), alt_mult=round(mult, 3),
                        asvi_now_incl_partial_week=round(now_incl_partial, 3),
                        last_complete_week=str((s[s.date + pd.Timedelta(days=6) <= pd.Timestamp(AS_OF)].date.iloc[-1]).date()),
                        topic=topics[tk]["label"]))
    out = pd.DataFrame(out)
    out.to_csv(ALT / "alt_scores.csv", index=False)
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
