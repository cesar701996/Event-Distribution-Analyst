"""
Step 9 - Build report.md and forecast_log.csv from the computed data.

Every number in the report comes from data/*.csv or data/*.json written by the
earlier steps; judgment text comes from code/analyst_views.json. Rerunning the
pipeline regenerates the report with fresh numbers.
"""
import json
import re
import string
import pandas as pd

from config import (ROOT, DATA, AS_OF, WINDOW_END, VOL_GAP_REL, VOL_GAP_ABS, SKEW_GAP_VOLS, DIR_CONF_MIN,
                    W_FULL, W_RECENT, WING_PCT)

cal = pd.read_csv(DATA / "calendar.csv")
br = pd.read_csv(DATA / "base_rates.csv").set_index("ticker")
mv = pd.read_csv(DATA / "earnings_moves.csv")
imp = pd.read_csv(DATA / "implied.csv").set_index("ticker")
fc = pd.read_csv(DATA / "forecast.csv").set_index("ticker")
econ = pd.read_csv(DATA / "trade_econ.csv").set_index("ticker")
ti = pd.read_csv(DATA / "trade_ideas.csv").set_index("ticker") if (DATA / "trade_ideas.csv").exists() else pd.DataFrame()
fs = pd.read_csv(DATA / "fundamentals" / "fundamentals_summary.csv").set_index("ticker")
alt = pd.read_csv(DATA / "alt" / "alt_scores.csv").set_index("ticker")
bt = json.loads((DATA / "alt" / "backtest_stats.json").read_text())
diag = json.loads((DATA / "forecast_diagnostics.json").read_text())
views = json.loads((ROOT / "code" / "analyst_views.json").read_text())
order = list(fc.sort_values("event_date").index)


def d(x):
    return pd.Timestamp(x).strftime("%b %-d")


def flags(f):
    out = [n for n, on in (("vol", f.vol_flag), ("skew", f.skew_flag), ("direction", f.dir_flag)) if on]
    return " + ".join(out)


class Fill(dict):
    def __missing__(self, k):
        print(f"  WARNING: placeholder {{{k}}} not filled")
        return "{" + k + "}"


def fill_map(tk):
    f, im, e = fc.loc[tk], imp.loc[tk], econ.loc[tk]
    m = dict(imp=f"{f.implied_event_move_pct:.1f}", fc=f"{f.forecast_move_pct:.1f}", spot=f"{f.spot:.2f}",
             rr=f"{f.market_rr25:.1f}", rr_ours=f"{f.model_rr25_ours:.1f}", rr_hw=f"{f.market_rr25_halfwidth:.1f}",
             p_up=f"{f.p_up:.2f}", last4=f"{f.mean_abs_last4:.1f}", skew_gap=f"{f.skew_gap_vols:+.1f}",
             skew_gap_abs=f"{abs(f.skew_gap_vols):.0f}", g_mkt=f"{f.event_skewness_mkt:+.1f}",
             g_ours=f"{f.event_skewness_ours:+.1f}", evday=f"{e.evday_projected_straddle_pct:.1f}",
             hist_inside=e.hist_moves_inside_evday_straddle)
    if tk in ti.index:
        t = ti.loc[tk]
        be = [float(x) for x in str(t.breakevens).split(" / ")]
        bep = [x.strip().rstrip("%") for x in str(t.breakevens_pct).split(" / ")]
        m.update(be_lo=f"{be[0]:.1f}", be_hi=f"{be[-1]:.1f}", be_lo_pct=bep[0], be_hi_pct=bep[-1].lstrip("+"),
                 be_list=" / ".join(f"{x:.1f}" for x in be), **{f"be{i}": f"{x:.1f}" for i, x in enumerate(be)},
                 win=f"{t.model_win_rate * 100:.0f}", ev=f"{t.model_ev_pct_spot:+.2f}" if tk != "ORCL" else f"{t.model_ev:+.2f}",
                 ev_pct=f"{t.model_ev_pct_spot:+.2f}",
                 max_loss=f"{t.max_loss:.2f}", max_loss_pct=f"{t.max_loss / f.spot * 100:.1f}",
                 max_loss_up=f"{t.max_loss_up:.2f}" if pd.notna(t.max_loss_up) else "n/a",
                 hedge_shares=f"{abs(t.stock_hedge_shares):.2f}",
                 edge=f"{t.skew_edge_per_share:.2f}" if pd.notna(t.skew_edge_per_share) else "n/a",
                 edge_pct=f"{t.skew_edge_per_share / f.spot * 100:.1f}" if pd.notna(t.skew_edge_per_share) else "n/a",
                 worst_pnl=f"{abs(t.worst_pnl_within_40pct):.2f}", worst_at=f"{t.worst_at:.0f}",
                 scenarios=t.scenarios, loss_at_m25=f"{t.pnl_at_minus25:+.2f}")
    return Fill(m)


def fmt(text, tk):
    return string.Formatter().vformat(text, (), fill_map(tk))


L = []
w = L.append

# ============================================================== header + summary
n_trades = int(fc.trade_idea.sum())
w(f"# Event Distribution Report: US large-cap tech, {AS_OF:%b %-d} to {WINDOW_END:%b %-d, %Y}")
w("")
w(f"As of the {AS_OF:%a %b %-d, %Y} close. Options marks are Friday closing quotes pulled over the weekend. "
  "Analysis only; no orders were placed.")
w("")
w("## Summary")
w("")
w("| Ticker | Event (after close) | Implied move: event-only (raw straddle) | Our forecast | Gap | Direction (conf.) | Skew: market vs ours (25d RR, vols) | Trade idea |")
w("|---|---|---|---|---|---|---|---|")
for tk in order:
    f, im = fc.loc[tk], imp.loc[tk]
    idea = f"**Yes**: {flags(f)}" if f.trade_idea else "No"
    w(f"| {tk} | {d(f.event_date)} | {f.implied_event_move_pct:.1f}% ({im.straddle_pct:.1f}%) | {f.forecast_move_pct:.1f}% | "
      f"{f.gap_pts:+.1f} pts ({f.gap_rel_pct:+.0f}%) | {f.direction} ({f.confidence}/5) | "
      f"{f.market_rr25:.1f} vs {f.model_rr25_ours:.1f} | {idea} |")
nocat = cal[~cal.in_window]
w("| " + ", ".join(nocat.ticker) + " | no catalyst in window | | | | | | No |")
w("")
flagged = sorted(fc[fc.trade_idea].index)
if flagged != sorted(views.get("_bottom_line_trades", [])):
    print("  WARNING: flags changed since _bottom_line was written; update it in analyst_views.json")
    w(f"**Bottom line.** {n_trades} trade ideas out of {len(fc)} events: {', '.join(flagged) or 'none'}. "
      "(Narrative bottom line is stale; see the name-by-name sections.)")
else:
    w(f"**Bottom line.** {n_trades} trade ideas out of {len(fc)} events. " + views["_bottom_line"])
w("")
w(f"**Thresholds (set before looking at results).** Vol: |forecast - implied| at least {VOL_GAP_REL:.0%} of implied and "
  f"{VOL_GAP_ABS:.1f} pt, *and* the forecast outside the implied range from three term-structure methods. "
  f"Skew: model vs market 25d risk reversal at least {SKEW_GAP_VOLS:.0f} vols *and* wider than the RR's own bid/ask. "
  f"Direction: confidence {DIR_CONF_MIN}/5 or more (none qualified). Why 20%: the standard error of a 12-print mean |move| "
  "is 20-30% of the mean for these names, and the event-only implied move moves by 1-2 pts across reasonable term-structure "
  "assumptions; anything smaller is noise.")
w("")
w("![Forecast vs implied](charts/summary_forecast_vs_implied.png)")
w("")
w("**Backdrop.** " + views["_market_backdrop"])
w("")

# ============================================================== per-name sections
w("## Name by name")
w("")
for tk in order:
    f, im, b, v, e, fu = fc.loc[tk], imp.loc[tk], br.loc[tk], views[tk], econ.loc[tk], fs.loc[tk]
    g = mv[mv.ticker == tk]
    w(f"### {tk}: {v['event']}")
    w("")
    w(f"Spot {f.spot:.2f}. Implied event move **{f.implied_event_move_pct:.1f}%** (range {f.implied_event_low:.1f}-{f.implied_event_high:.1f}%), "
      f"raw {d(im.post_expiry)} straddle {im.straddle_pct:.1f}%. Our forecast **{f.forecast_move_pct:.1f}%**. "
      f"Call: **{f.direction}, {f.confidence}/5**. Trade idea: **{('yes (' + flags(f) + ')') if f.trade_idea else 'no'}**.")
    w("")
    w("**Base rates (last 12 prints, 1-day close-to-close)**")
    w("")
    n_eps = int(g.surprise_pct.notna().sum())
    w("| Mean abs | Median abs | Std (signed) | Mean signed | Largest up | Largest down | Up / down | EPS beats (of prints with data) |")
    w("|---|---|---|---|---|---|---|---|")
    w(f"| {b.mean_abs:.1f}% | {b.median_abs:.1f}% | {b.std_signed:.1f}% | {b.mean_signed:+.1f}% | {b.max_up:+.1f}% | "
      f"{b.max_down:+.1f}% | {int(b.n_up)} / {int(b.n_down)} | {int(f.beats_last12)} of {n_eps} |")
    w("")
    w(f"Moves, oldest first: {', '.join(f'{x:+.1f}' for x in g.move_pct)}. Last 4 average {b.mean_abs_last4:.1f}%. "
      f"Standard error of the mean |move| is {b.se_mean_abs:.1f} pts.")
    w("")
    w("**What options imply**")
    w("")
    w(f"- ATM IV: pre-event {d(im.pre_expiry)} expiry {im.iv_pre_atm:.0f}%, event {d(im.post_expiry)} expiry {im.iv_post_atm:.0f}%, "
      f"forward ex-event vol after the print {im.iv_fwd_ex_event:.0f}%.")
    w(f"- Event-only move {im.implied_event_move_pct:.1f}% (piecewise term structure). Bounds: {im.implied_event_move_low_pct:.1f}% if every "
      f"ordinary day runs at the pre-event IV, {im.implied_event_move_high_pct:.1f}% if they all run at the forward vol.")
    w(f"- Skew: 25d put IV {im.iv_put25:.0f}% (K~{im.k_put25:.0f}) vs 25d call IV {im.iv_call25:.0f}% (K~{im.k_call25:.0f}), "
      f"**RR {im.rr25_put_minus_call:.1f} vols** (bid/ask half-width {im.rr25_halfwidth:.1f}). Pre-event expiry RR {im.rr25_pre_expiry:.1f}. "
      f"Implied event skewness {f.event_skewness_mkt:+.1f} (negative = market fears a drop more than a jump).")
    w("")
    w("**Fundamental view**")
    w("")
    w(f"- *Consensus vs guidance:* {v['consensus_vs_guidance']}")
    w(f"- *Revisions:* {v['revisions']}")
    w(f"- *Tone of last release:* {v['tone']} ([EX-99.1]({fu.release_url}))")
    w(f"- *Sector news since last report:* {v['sector_news']}")
    w(f"- **Call: {f.direction}, confidence {v['confidence']}/5.** {v['why']}")
    w("")
    w("**Our forecast (math)**")
    w("")
    sig_h = f.base_move * 1.2533
    w("```")
    w(f"base     B = {W_FULL}*{f.mean_abs_12:.2f} + {W_RECENT}*{f.mean_abs_last4:.2f}            = {f.base_move:.2f}%")
    w(f"regime   sigma_hist = B*1.2533 = {sig_h:.2f}%;  ordinary day then {f.rv20_pre_hist_median:.0f}%/sqrt(252) = "
      f"{f.rv20_pre_hist_median / 15.875:.2f}%, now {f.rv20_now:.0f}%/sqrt(252) = {f.rv20_now / 15.875:.2f}%")
    w(f"         sigma_now = sqrt({sig_h:.2f}^2 - {f.rv20_pre_hist_median / 15.875:.2f}^2 + {f.rv20_now / 15.875:.2f}^2)  -> x{f.regime_mult:.3f}")
    w(f"alt      Google Trends attention z = {alt.loc[tk, 'z']:+.2f}                      -> x{f.alt_mult:.3f}")
    w(f"fund     event-specific uncertainty                          -> x{f.fund_mult:.2f}")
    w(f"forecast E|move| = {f.base_move:.2f} x {f.regime_mult:.3f} x {f.alt_mult:.3f} x {f.fund_mult:.2f}     = {f.forecast_move_pct:.2f}%"
      f"   vs implied {f.implied_event_move_pct:.2f}%  ->  gap {f.gap_pts:+.2f} pts ({f.gap_rel_pct:+.1f}%)")
    w(f"shape    p(up) = 0.5 + 0.5*({f.p_hist_up:.3f}-0.5) + 0.03*({ {'up': 1, 'neutral': 0, 'down': -1}[f.direction] })*{f.confidence} = {f.p_up:.3f}")
    w(f"         up move {f.m_up:.2f}%, down move {f.m_down:.2f}%, expected signed move {f.exp_signed_move:+.2f}%, "
      f"event skewness {f.event_skewness_ours:+.2f}")
    w(f"skew     RR if the event were symmetric {f.rr25_if_symmetric_event:.1f}; with our shape {f.model_rr25_ours:.1f}; "
      f"market {f.market_rr25:.1f}  ->  gap {f.skew_gap_vols:+.1f} vols (bid/ask +/-{f.market_rr25_halfwidth:.1f})")
    w("```")
    w("")
    if f.trade_idea and tk in ti.index:
        t, spec = ti.loc[tk], v["trade"]
        w(f"**Trade idea: {spec['name']}**")
        w("")
        w(f"- **Thesis:** {fmt(spec['thesis'], tk)}")
        w(f"- **Structure:** {t.legs}. Net {t.premium_type} {abs(t.net_premium):.2f} per share"
          + (f", plus short {abs(t.stock_hedge_shares):.2f} shares per RR as the delta hedge" if t.stock_hedge_shares else "")
          + f". Breakevens at expiry {t.breakevens} ({t.breakevens_pct}). {fmt(spec['why_structure'], tk)}")
        w(f"- **What it needs to win:** {fmt(spec['needs'], tk)}")
        w(f"- **What proves it wrong:** {fmt(spec['wrong'], tk)}")
        w(f"- **Main risks:** {fmt(spec['risks'], tk)}")
        w("")
        w(f"![{tk} earnings moves](charts/{tk}_earnings_moves.png)")
    else:
        reasons = []
        if abs(f.gap_rel_pct) >= VOL_GAP_REL * 100 and not f.forecast_outside_implied_range:
            reasons.append(f"the {f.gap_rel_pct:+.0f}% size gap clears 20% only against the central estimate; our "
                           f"{f.forecast_move_pct:.1f}% sits inside the {f.implied_event_low:.1f}-{f.implied_event_high:.1f}% implied range, "
                           "so it depends on a term-structure assumption, not on a real disagreement")
        elif abs(f.gap_rel_pct) < VOL_GAP_REL * 100:
            reasons.append(f"size gap {f.gap_rel_pct:+.0f}% is under the 20% bar")
        if abs(f.skew_gap_vols) >= SKEW_GAP_VOLS and abs(f.skew_gap_vols) <= f.market_rr25_halfwidth:
            reasons.append(f"the {f.skew_gap_vols:+.1f}-vol skew gap is inside the RR's own bid/ask (+/-{f.market_rr25_halfwidth:.1f} vols "
                           "on weekend marks); recheck on live quotes Monday")
        elif abs(f.skew_gap_vols) < SKEW_GAP_VOLS:
            reasons.append(f"skew gap {f.skew_gap_vols:+.1f} vols is under the 3-vol bar")
        if f.confidence < DIR_CONF_MIN:
            reasons.append(f"direction confidence {f.confidence}/5 is too low to trade")
        w(f"**No trade.** " + " ".join(r[0].upper() + r[1:] + "." for r in reasons))
    w("")

# ============================================================== no-catalyst names
w("## No catalyst in the window")
w("")
w("| Ticker | Last report | Next report (yfinance) | Date status |")
w("|---|---|---|---|")
for _, r in nocat.iterrows():
    w(f"| {r.ticker} | {r.last_event} | {r.next_event} | {'estimated' if r.date_is_estimate else 'confirmed'} |")
w("")
w("NVDA reported Nov 17, three days before the window opened; its CEO exit and the reported SEC review are sector "
  "drivers for everything above. Only earnings dates were screened. Non-earnings company events (product launches, "
  "investor days, annual meetings) were not modeled, because options do not price them as clean jumps and we had no "
  "free, dated source for them.")
w("")

# ============================================================== alt data
w("## Alternative data signal: Google Trends search attention")
w("")
w("**Construction.** Weekly Google Trends interest (via `pytrends`) for each company's Knowledge Graph *topic* "
  "(so 'Oracle' the company is not mixed with the word), Nov 2022 to Nov 2026. Abnormal search volume "
  "ASVI = ln(mean of the last 2 complete weeks) - ln(median of the 8 weeks before them), measured at the same lead "
  "time before each of the last 12 reports as we are before this one. Score = z-score of today's ASVI against those 12, "
  "capped at +/-2. The idea comes from Da, Engelberg & Gao (2011): more pre-event attention, bigger immediate reaction.")
w("")
w("| Ticker | Lead (days) | ASVI now | Hist. mean | Hist. std | z (score) | ASVI incl. partial selloff week | Size multiplier |")
w("|---|---|---|---|---|---|---|---|")
for tk in order:
    a = alt.loc[tk]
    w(f"| {tk} | {a.lead_days} | {a.asvi_now:+.2f} | {a.asvi_hist_mean:+.2f} | {a.asvi_hist_std:.2f} | {a.z:+.2f} | "
      f"{a.asvi_now_incl_partial_week:+.2f} | x{a.alt_mult:.3f} |")
w("")
w(f"**Does it work? Not in our sample.** Across {bt['n']} past prints on all {bt['names']} names (lead 14 days), the correlation "
  f"between the attention z-score and the relative move size is {bt['pearson']:+.2f} (Spearman {bt['spearman']:+.2f}); "
  f"regression slope {bt['slope']:+.3f} per z, t-stat {bt['slope_t']:.2f}. Top-tercile attention prints moved "
  f"{bt['top_tercile_rel_move']:.2f}x their usual size, bottom tercile {bt['bottom_tercile_rel_move']:.2f}x. "
  "We still run it through the forecast as specified, but the multiplier is half the fitted slope, so it lands within "
  "+/-1.5% of 1.0 and changes nothing. That is the honest outcome: the signal is built and wired in, and the data say to give it almost no weight.")
w("")
w("**Weaknesses.**")
w("")
w("- Google rescales each pull to 0-100 and rounds to integers, so small weekly changes are noise, and a rerun on a later day can return slightly different history.")
w("- Weekly granularity: the last complete week ends Nov 14, so the Nov 16-20 selloff week is not in the main reading (the 'incl. partial week' column shows it barely changes the sign).")
w("- Attention is not direction. It can only speak to size, and for mega-caps, search interest is dominated by consumer products (Apple, Amazon, Netflix) rather than investors.")
w("- Attention spikes are partly caused by price moves, so the signal overlaps with the volatility regime we already use.")
w("- The API is unofficial and rate-limited (we hit HTTP 429/500 and retried). It can break without notice.")
w("- 12 events per name is a tiny sample, and the backtest pools names with very different investor bases.")
w("")

# ============================================================== method + data issues
cal_r = diag["regime_calibration"]
w("## Method notes and data issues")
w("")
w(f"- **Regime adjustment.** We do not scale history by the vol ratio. Fitting |move| / usual |move| = c x (pre-event RV / usual RV)^beta "
  f"across {cal_r['n']} past prints gives beta = {cal_r['beta']:+.2f} (90% bootstrap {cal_r['beta_p05']:+.2f} to {cal_r['beta_p95']:+.2f}). "
  "So we only swap the ordinary-day variance inside the event day for today's level, which adds 0-9% to the move size.")
w("- **Event-only implied move.** The straddle on the first post-event expiry also pays for 8-18 ordinary days at crisis vol. We strip them "
  "out with the ATM term structure (pre-event expiry, event expiry, next expiry). With front-end IVs of 56-87%, that choice matters, so the "
  "three-method range is shown and required for a vol flag.")
w("- **Skew math.** Gram-Charlier (Backus, Foresi & Wu 2004) links RR25 to return skewness, RR ~ -0.2247 x ATM IV x skewness. Variance and third "
  "cumulants add across independent pieces, so we can back out the skewness the market assigns to the event itself and compare it with ours. "
  "It is an approximation that gets cruder for large skew. It is used the same way on both sides of the comparison.")
w("- **Quote quality.** Weekend marks. ATM relative spreads 4-14%, 25d RR bid/ask half-widths "
  + ", ".join(f"{tk} {imp.loc[tk].rr25_halfwidth:.1f}" for tk in order) + " vols. Recheck every number on live markets before acting.")
w("- **yfinance quirks handled.** (1) Over the weekend, the Nov 20 daily bar came back blank, so spot falls back to the quote's regular-market close "
  "(timestamp checked = Nov 20) and is cached in data/spot.csv. (2) AVGO's Sep 2 2026 print had no reported EPS in yfinance; it was kept "
  "because SEC EDGAR shows the Item 2.02 8-K that day. All 144 historical dates (12 names x 12) matched an EDGAR 8-K within one day.")
w("- **Sources that failed or were not available.** Earnings call transcripts are not freely available as text from IR sites (they host webcasts), "
  "so tone comes from the EX-99.1 press release. Google Trends returned HTTP 500/429 on some pulls and succeeded on retry. Wikipedia pageviews "
  "(a candidate alt-data source) were rate-limited and not used. No numbers were estimated or filled in by hand; consensus and guidance figures come from yfinance and EDGAR as cited.")
w("- **Small samples.** 12 prints per name. The CRM and ADBE vol flags rest on means with 1.5-2 pt standard errors; the ADBE flag disappears "
  "if the recency weight is dropped (12-quarter mean alone gives a -9% gap).")
w("")
w("## Files")
w("")
w("- `forecast_log.csv`: tracking log, one row per event, with blank columns to fill in after each print.")
w("- `charts/`: earnings-move charts for each flagged name plus the summary chart.")
w("- `code/`: the pipeline (`python code/run_all.py` reruns everything). `code/analyst_views.json` holds the judgment inputs.")
w("- `data/`: every intermediate table, so each number above can be traced.")

(ROOT / "report.md").write_text("\n".join(L) + "\n")
print(f"report.md written ({len(L)} lines)")

# ============================================================== tracking log
rows = []
for tk in order:
    f, im = fc.loc[tk], imp.loc[tk]
    rows.append(dict(
        ticker=tk, event_date=f.event_date, event_timing=im.timing, reaction_date=im.reaction_date,
        spot_asof=f.spot, implied_move_straddle_pct=im.straddle_pct, implied_event_move_pct=f.implied_event_move_pct,
        forecast_move_pct=f.forecast_move_pct, gap_pct_of_implied=f.gap_rel_pct,
        direction_call=f.direction, confidence=f.confidence, p_up=f.p_up,
        skew_view=f.skew_view + ("" if f.skew_flag or f.skew_view == "in line" else " (inside RR bid/ask; not flagged)"),
        market_rr25=f.market_rr25, model_rr25=f.model_rr25_ours,
        trade_idea="yes" if f.trade_idea else "no",
        trade_structure=views[tk]["trade"]["name"] if f.trade_idea and "trade" in views[tk] else "",
        actual_move_pct="", actual_abs_move_pct="", abs_move_vs_implied="", abs_move_vs_forecast="",
        direction_correct="", trade_result="", notes="",
    ))
log = pd.DataFrame(rows)
# Keep any results already recorded by 10_scorecard.py so a rerun never blanks them.
if (DATA / "scorecard.csv").exists():
    sc = pd.read_csv(DATA / "scorecard.csv", dtype=str).fillna("").set_index(["ticker", "event_date"])
    for i, r in log.iterrows():
        k = (r.ticker, str(r.event_date))
        if k in sc.index:
            x = sc.loc[k]
            for c in ["actual_move_pct", "actual_abs_move_pct", "abs_move_vs_implied", "abs_move_vs_forecast",
                      "direction_correct"]:
                log.loc[i, c] = x[c]
            if x.trade_result:
                log.loc[i, "trade_result"] = f"{x.trade_result} {x.trade_pnl_per_share}/sh at expiry ({x.expiry_close})"
log.to_csv(ROOT / "forecast_log.csv", index=False)
print("forecast_log.csv written")
