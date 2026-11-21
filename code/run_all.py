"""
Rerun the whole pipeline in order.

    python code/run_all.py              # full run (Google Trends uses the cache in data/alt/ if present)
    python code/run_all.py --from 6     # rerun from step 6 onward (e.g. after editing analyst_views.json)

To move the analysis to a new date: change AS_OF in code/config.py, delete data/spot.csv and the
data/alt/trends_*.csv cache, update analyst_views.json, then run this script.
"""
import subprocess
import sys
from pathlib import Path

STEPS = ["01_calendar.py", "02_base_rates.py", "03_implied_move.py", "04_fundamentals.py",
         "05_alt_data_trends.py", "06_forecast.py", "07_trade_ideas.py", "08_charts.py", "09_build_report.py"]


def main():
    start = 1
    if "--from" in sys.argv:
        start = int(sys.argv[sys.argv.index("--from") + 1])
    here = Path(__file__).resolve().parent
    for i, step in enumerate(STEPS, 1):
        if i < start:
            continue
        print(f"\n=== [{i}/{len(STEPS)}] {step} ===", flush=True)
        r = subprocess.run([sys.executable, step], cwd=here)
        if r.returncode != 0:
            sys.exit(f"{step} failed (exit {r.returncode}); fix it and rerun with --from {i}")
    print("\nDone: report.md, forecast_log.csv, charts/ updated.")


if __name__ == "__main__":
    main()
