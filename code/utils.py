"""
Helpers: trading-day counting, Black-Scholes pricing / implied vol / delta,
and a polite HTTP getter for SEC EDGAR.
"""
import time
import numpy as np
import requests
from scipy.stats import norm
from scipy.optimize import brentq

from config import HOLIDAYS, SEC_USER_AGENT

HOL = np.array(HOLIDAYS, dtype="datetime64[D]")


def trading_days(start, end):
    """Trading days in (start, end], i.e. excludes start, includes end."""
    s = np.datetime64(str(start)) + np.timedelta64(1, "D")
    e = np.datetime64(str(end)) + np.timedelta64(1, "D")
    return int(np.busday_count(s, e, holidays=HOL))


def next_trading_day(d):
    return np.busday_offset(np.datetime64(str(d)), 1, roll="forward", holidays=HOL).astype(object)


# ---------------------------------------------------------------- Black-Scholes (continuous r and q)
def bs_price(S, K, T, sigma, r=0.0, q=0.0, kind="c"):
    if T <= 0 or sigma <= 0:
        return max(0.0, (S - K) if kind == "c" else (K - S))
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if kind == "c":
        return S * np.exp(-q * T) * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    return K * np.exp(-r * T) * norm.cdf(-d2) - S * np.exp(-q * T) * norm.cdf(-d1)


def bs_delta(S, K, T, sigma, r=0.0, q=0.0, kind="c"):
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    return np.exp(-q * T) * norm.cdf(d1) if kind == "c" else -np.exp(-q * T) * norm.cdf(-d1)


def implied_vol(price, S, K, T, r=0.0, q=0.0, kind="c"):
    """Invert Black-Scholes with Brent's method. Returns NaN if price is outside no-arb bounds."""
    intrinsic = bs_price(S, K, T, 1e-9, r, q, kind)
    if not np.isfinite(price) or price <= intrinsic + 1e-6:
        return np.nan
    try:
        return brentq(lambda v: bs_price(S, K, T, v, r, q, kind) - price, 1e-4, 6.0, xtol=1e-7)
    except ValueError:
        return np.nan


# ---------------------------------------------------------------- SEC EDGAR
def sec_get(url, sleep=0.15, **kw):
    """GET with the SEC-required User-Agent and a small pause (SEC limit is 10 req/s)."""
    time.sleep(sleep)
    r = requests.get(url, headers={"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"},
                     timeout=30, **kw)
    r.raise_for_status()
    return r


# ---------------------------------------------------------------- spot price as of AS_OF
def spot_close(ticker, as_of):
    """
    Closing price on `as_of`. yfinance sometimes blanks the latest daily bar over a
    weekend, so if the history row is missing we fall back to the quote's
    regularMarketPrice, but only if its timestamp is on `as_of`. Result is cached in
    data/spot.csv so every script uses the same number.
    """
    import datetime as _dt
    import pandas as _pd
    import yfinance as _yf
    from config import DATA
    cache = DATA / "spot.csv"
    if cache.exists():
        c = _pd.read_csv(cache)
        hit = c[(c.ticker == ticker) & (c.as_of == str(as_of))]
        if len(hit):
            return float(hit.close.iloc[0])
    t = _yf.Ticker(ticker)
    h = t.history(start=str(as_of - _dt.timedelta(days=7)), end=str(as_of + _dt.timedelta(days=1)))["Close"].dropna()
    h.index = h.index.tz_localize(None).date
    if len(h) and h.index[-1] == as_of:
        px, src = float(h.iloc[-1]), "history"
    else:
        info = t.info
        ts = _dt.datetime.fromtimestamp(info["regularMarketTime"], _dt.timezone.utc).date()
        if ts != as_of:
            raise RuntimeError(f"{ticker}: no close available for {as_of}")
        px, src = float(info["regularMarketPrice"]), "quote regularMarketPrice"
    row = _pd.DataFrame([dict(ticker=ticker, as_of=str(as_of), close=px, source=src)])
    row.to_csv(cache, mode="a", header=not cache.exists(), index=False)
    return px
