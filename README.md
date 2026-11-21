# Event Distribution Analyst

An agent plus a rerunnable Python pipeline that forecasts how US large-cap tech stocks
will move around earnings, compares that with what the options market is pricing, and
writes up trade ideas (or "no trade") for a market-making desk.

For each event it looks at three things: **direction** (mean), **size** (volatility) and
**shape** (skew: is a big drop or a big jump more likely).

> Analysis only. Nothing here places or simulates orders. Not investment advice.

## Latest run: as of Fri Nov 20, 2026

| Ticker | Event | Implied event move (raw straddle) | Our forecast | Direction | Trade idea |
|---|---|---|---|---|---|
| CRM | Dec 2 | 10.3% (13.1%) | 7.5% | up, 3/5 | Yes: short iron fly, put wing pushed out (vol + skew) |
| AVGO | Dec 9 | 8.7% (16.7%) | 10.6% | neutral, 2/5 | No |
| ADBE | Dec 9 | 9.4% (13.6%) | 7.0% | down, 2/5 | Yes: short iron fly, half size (vol) |
| ORCL | Dec 14 | 11.5% (20.7%) | 11.6% | neutral, 2/5 | Yes: delta-hedged long 25d risk reversal (skew) |

The other eight names have no earnings in the window. Full write-up: **[report.md](report.md)**.
Tracking log: **[forecast_log.csv](forecast_log.csv)**.

## The agent

`.claude/agents/event-distribution-analyst.md` is a Claude Code subagent. Open this repo in
Claude Code and ask for it, for example:

```
Use the event-distribution-analyst agent to refresh the report as of today.
```

The agent runs the pipeline, reads the press releases and consensus data it pulls, writes its
fundamental judgment into `code/analyst_views.json`, reruns the forecast, prices any flagged
trades, and rebuilds the report. Its rules: free data only, never invent a number, thresholds
fixed before looking at results, and "no trade" when the gap is small or the data are thin.

## Run the pipeline yourself

```bash
pip install -r requirements.txt
```

Set your SEC contact once (SEC requires a name and email on automated requests; it stays out of the repo). In PowerShell:

```bash
setx SEC_USER_AGENT "Your Name event-research script (you@domain.edu)"
```

Then open a new terminal and run:

```bash
python code/run_all.py
```

```bash
python code/run_all.py --from 6
```

The second form reruns from the forecast step after you edit `code/analyst_views.json`.
To change the as-of date, edit `AS_OF` in `code/config.py`, delete `data/spot.csv` and
`data/alt/trends_*.csv`, and update the views file.

| Step | Script | What it does |
|---|---|---|
| 1 | `01_calendar.py` | Earnings dates (yfinance) and SEC EDGAR 8-K Item 2.02 filings; flags events in the 30-day window |
| 2 | `02_base_rates.py` | 1-day reaction to the last 12 prints, cross-checked against EDGAR; mean, median, std, extremes, up/down split |
| 3 | `03_implied_move.py` | ATM straddle on the first post-event expiry, event-only move from the term structure, 25d put minus call IV with bid/ask width |
| 4 | `04_fundamentals.py` | Consensus, revisions, price targets, latest EX-99.1 press release text and guidance snippets, headlines |
| 5 | `05_alt_data_trends.py` | Google Trends abnormal search attention, z-scored and backtested on 144 past prints |
| 6 | `06_forecast.py` | Forecast of size, direction and shape; comparison with the market; flags |
| 7 | `07_trade_ideas.py` | Prices flagged structures at the marks, payoff grid, breakevens, model win rate and EV |
| 8 | `08_charts.py` | Earnings-move charts for flagged names and a summary chart |
| 9 | `09_build_report.py` | Writes `report.md` and `forecast_log.csv` |

Judgment inputs (direction, confidence, notes, trade legs) live in `code/analyst_views.json`.
Every number in the report traces to a file in `data/`.

## Method in brief

- **Forecast size** = 0.6 x mean |move| (12 prints) + 0.4 x mean |move| (last 4), with today's
  ordinary-day variance swapped into the event day, times an alt-data multiplier and an
  event-specific uncertainty multiplier.
- **Implied size** strips ordinary days out of the straddle using the ATM term structure;
  three methods give a range.
- **Skew** compares the 25d risk reversal with what our event shape implies, via
  Gram-Charlier and the fact that variances and third cumulants add.
- **Flags**: size gap of at least 20% and 1 pt and outside the implied range; skew gap of at least
  3 vols and wider than the quote; direction confidence of at least 4/5.

## Known limits

- 12 prints per name is a small sample; standard errors on the mean |move| run 1.5-2.5 pts.
- The run uses Friday-close option marks pulled over the weekend; quotes were wide. Recheck live.
- Earnings call transcripts are not freely available as text, so tone comes from press releases.
- The Google Trends signal showed no predictive power in its backtest and carries almost no weight.
