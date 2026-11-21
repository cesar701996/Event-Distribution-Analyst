"""
Shared settings for the Event Distribution Analyst pipeline.

Every script imports from here, so changing the as-of date, the universe or a
threshold in one place changes it everywhere. Rerun with:  python code/run_all.py
"""
import os
from pathlib import Path
import datetime as dt

# ---------------------------------------------------------------- paths
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CHARTS = ROOT / "charts"
DATA.mkdir(exist_ok=True)
CHARTS.mkdir(exist_ok=True)

# ---------------------------------------------------------------- universe and window
UNIVERSE = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",
            "AVGO", "ORCL", "CRM", "ADBE", "AMD", "NFLX"]
AS_OF = dt.date(2026, 11, 20)          # "today" for the analysis (Friday close)
WINDOW_DAYS = 30                       # look-ahead window
WINDOW_END = AS_OF + dt.timedelta(days=WINDOW_DAYS)

# ---------------------------------------------------------------- base rates
N_EVENTS = 12                          # last N reported earnings used for base rates

# ---------------------------------------------------------------- market calendar
# NYSE full-day closures that fall inside the analysis horizon. Used to count
# trading days to expiry. (Nov 27 2026 is a half day; we count it as a full day.)
HOLIDAYS = ["2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18"]

# ---------------------------------------------------------------- SEC EDGAR
# SEC asks automated clients to identify themselves with a name and contact email.
# Set it in the SEC_USER_AGENT environment variable so it never gets committed, e.g.
#   PowerShell:  setx SEC_USER_AGENT "Your Name event-research script (you@domain.edu)"
#   bash:        export SEC_USER_AGENT="Your Name event-research script (you@domain.edu)"
SEC_USER_AGENT_DEFAULT = "EventDistributionAnalyst research script (contact not set)"
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", SEC_USER_AGENT_DEFAULT)

# ---------------------------------------------------------------- forecast knobs (see report "Method")
W_FULL, W_RECENT = 0.6, 0.4            # base move = 0.6*mean|move| (12) + 0.4*mean|move| (last 4)
# Regime: today's normal-day variance is ADDED to the historical earnings-information
# variance (see 06_forecast.py). We do not scale the whole move by a vol ratio because,
# across the 144 past events, pre-event realized vol did not predict move size.
MC_PATHS = 400_000                     # Monte Carlo samples for the model smile
MC_SEED = 7
WING_PCT = 0.15                        # iron-fly wings at +/-15% of spot for the vol-trade evaluation
ALT_CAP = (0.90, 1.10)                 # Google Trends attention multiplier bounds
DIR_TILT_PER_CONF = 0.03               # p(up) tilt per confidence point from the fundamental call
P_UP_CAP = (0.25, 0.75)
MAG_SHRINK = 0.5                       # shrink up/down magnitudes 50% toward overall mean |move|

# ---------------------------------------------------------------- decision thresholds
VOL_GAP_REL = 0.20                     # flag if |forecast - implied| / implied >= 20% ...
VOL_GAP_ABS = 1.0                      # ... and the gap is at least 1.0 percentage point
                                       # ... and the forecast is outside the implied range (3 methods)
SKEW_GAP_VOLS = 3.0                    # flag if |model RR25 - market RR25| >= 3 vol points
                                       # ... and the gap is wider than the RR's own bid/ask half-width
DIR_CONF_MIN = 4                       # flag a directional idea only at confidence >= 4
