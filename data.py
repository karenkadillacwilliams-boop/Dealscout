"""Price fetching, period returns, momentum grading, and Power Gauge wiring."""
from __future__ import annotations

import logging
import math

import pandas as pd
import streamlit as st
import yfinance as yf

from power_gauge import calculate_power_gauge

# yfinance logs a WARNING for every delisted/mis-typed ticker on every fetch.
# Those messages are cosmetic — the fetch already handles missing columns
# correctly — so we silence them to keep the server log readable. Errors
# (not warnings) still propagate.
logging.getLogger("yfinance").setLevel(logging.ERROR)


@st.cache_data(ttl=900, show_spinner="Fetching prices from Yahoo Finance...")
def fetch_history(tickers: list[str], period: str = "6mo") -> pd.DataFrame:
    """Return a DataFrame of adjusted-close prices indexed by date, columns = tickers."""
    if not tickers:
        return pd.DataFrame()
    raw = yf.download(
        tickers=" ".join(tickers),
        period=period,
        interval="1d",
        auto_adjust=True,
        progress=False,
        group_by="ticker",
        threads=True,
    )
    if raw.empty:
        return pd.DataFrame()

    if isinstance(raw.columns, pd.MultiIndex):
        closes = pd.DataFrame({t: raw[t]["Close"] for t in tickers if t in raw.columns.get_level_values(0)})
    else:
        closes = raw[["Close"]].rename(columns={"Close": tickers[0]})
    return closes.dropna(how="all").sort_index()


def period_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Compute daily / weekly / monthly % return for each ticker from a price history frame."""
    if prices.empty:
        return pd.DataFrame(columns=["ticker", "last", "daily_pct", "weekly_pct", "monthly_pct", "ytd_pct"])

    rows = []
    for ticker in prices.columns:
        series = prices[ticker].dropna()
        if series.empty:
            continue
        last = float(series.iloc[-1])
        rows.append({
            "ticker": ticker,
            "last": last,
            "daily_pct":   _pct_change(series, 1),
            "weekly_pct":  _pct_change(series, 5),
            "monthly_pct": _pct_change(series, 21),
            "ytd_pct":     _ytd_change(series),
        })
    return pd.DataFrame(rows)


def _ytd_change(series: pd.Series) -> float:
    current_year = series.index[-1].year
    year_start = series.loc[series.index.year == current_year]
    if year_start.empty or len(year_start) < 2:
        return float("nan")
    first = float(year_start.iloc[0])
    if first == 0:
        return float("nan")
    return (float(series.iloc[-1]) / first - 1.0) * 100.0


def _pct_change(series: pd.Series, lookback_bars: int) -> float:
    if len(series) <= lookback_bars:
        return float("nan")
    prev = float(series.iloc[-1 - lookback_bars])
    if prev == 0:
        return float("nan")
    return (float(series.iloc[-1]) / prev - 1.0) * 100.0


def momentum_grade(daily: float, weekly: float, monthly: float, portfolio_weekly_avg: float) -> str:
    """Phase-1 grading: weighted momentum vs. portfolio average → A-F letter."""
    if any(pd.isna(x) for x in (daily, weekly, monthly, portfolio_weekly_avg)):
        return "—"
    score = 0.2 * daily + 0.3 * weekly + 0.5 * monthly
    relative = score - portfolio_weekly_avg
    if   relative >=  8: return "A"
    elif relative >=  3: return "B"
    elif relative >= -2: return "C"
    elif relative >= -7: return "D"
    else:                return "F"


def add_grades(returns_df: pd.DataFrame) -> pd.DataFrame:
    """Add a 'grade' column to a returns DataFrame using portfolio-relative momentum."""
    if returns_df.empty:
        return returns_df
    avg = returns_df["weekly_pct"].mean(skipna=True)
    returns_df = returns_df.copy()
    returns_df["grade"] = returns_df.apply(
        lambda r: momentum_grade(r["daily_pct"], r["weekly_pct"], r["monthly_pct"], avg),
        axis=1,
    )
    return returns_df


# ──────────────────────────────────────────────────────────────────────────────
# Power Gauge wiring
# ──────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner="Fetching OHLCV bars...")
def fetch_ohlcv(tickers: list[str], period: str = "1y") -> dict[str, pd.DataFrame]:
    """Return a dict of ticker → full OHLCV DataFrame."""
    if not tickers:
        return {}
    raw = yf.download(
        tickers=" ".join(tickers),
        period=period,
        interval="1d",
        auto_adjust=True,
        progress=False,
        group_by="ticker",
        threads=True,
    )
    if raw.empty:
        return {}
    out: dict[str, pd.DataFrame] = {}
    if isinstance(raw.columns, pd.MultiIndex):
        for t in tickers:
            if t in raw.columns.get_level_values(0):
                df = raw[t].dropna(how="all")
                if not df.empty:
                    out[t] = df
    else:
        out[tickers[0]] = raw.dropna(how="all")
    return out


@st.cache_data(ttl=3600, show_spinner="Fetching market benchmark (SPY)...")
def fetch_spy_closes(period: str = "1y") -> pd.Series:
    df = yf.download("SPY", period=period, interval="1d", auto_adjust=True, progress=False)
    if df.empty:
        return pd.Series(dtype=float)
    if isinstance(df.columns, pd.MultiIndex):
        return df["Close"].iloc[:, 0]
    return df["Close"]


@st.cache_data(ttl=3600, show_spinner="Fetching fundamentals...")
def fetch_fundamentals(ticker: str) -> dict:
    """Pull Yahoo .info + earnings_history for a single ticker, normalized into the
    dict shape Power Gauge expects."""
    tk = None
    info = {}
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
    except Exception:
        pass

    earnings_history: list[dict] = []
    try:
        eh = tk.earnings_history if tk is not None else None
        if isinstance(eh, pd.DataFrame) and not eh.empty:
            for _, row in eh.iterrows():
                actual = row.get("epsActual") if "epsActual" in row else row.get("Reported EPS")
                surprise = row.get("surprisePercent") if "surprisePercent" in row else row.get("Surprise(%)")
                if actual is None or (isinstance(actual, float) and math.isnan(actual)):
                    continue
                earnings_history.append({
                    "actual": float(actual),
                    "surprisePct": float(surprise) if surprise is not None and not (isinstance(surprise, float) and math.isnan(surprise)) else None,
                })
            earnings_history.reverse()  # newest-first to match JS expectation
    except Exception:
        pass

    return {
        "symbol": ticker,
        "sector": info.get("sector"),
        "debtToEquity": info.get("debtToEquity"),
        "priceToBook": info.get("priceToBook"),
        "returnOnEquity": info.get("returnOnEquity"),
        "priceToSalesTrailing12Months": info.get("priceToSalesTrailing12Months"),
        "freeCashflow": info.get("freeCashflow"),
        "marketCap": info.get("marketCap"),
        "totalRevenue": info.get("totalRevenue"),
        "forwardPE": info.get("forwardPE"),
        "earningsGrowth": info.get("earningsGrowth"),
        "shortPercentOfFloat": info.get("shortPercentOfFloat"),
        "recommendationMean": info.get("recommendationMean"),
        "recommendationKey": info.get("recommendationKey"),
        "earningsHistory": earnings_history,
    }


@st.cache_data(ttl=3600, show_spinner="Computing Power Gauge ratings...")
def compute_power_gauge_ratings(tickers: list[str]) -> pd.DataFrame:
    """Compute Power Gauge ratings for the universe and return a flat DataFrame."""
    bars_map = fetch_ohlcv(tickers, period="1y")
    spy = fetch_spy_closes(period="1y")
    rows = []
    for t in tickers:
        bars = bars_map.get(t)
        if bars is None or len(bars) < 30:
            rows.append({"ticker": t, "label": "—", "score": None, "adjustment": "no-bars",
                         "fin": None, "earn": None, "tech": None, "exp": None, "narrative": ""})
            continue
        funds = fetch_fundamentals(t)
        rating = calculate_power_gauge(t, bars, funds, spy)
        cats = rating.get("categories") or {}
        rows.append({
            "ticker": t,
            "label": rating["overall"]["label"],
            "score": rating["overall"]["score"],
            "adjustment": rating["overall"]["adjustment"],
            "fin":  cats.get("financials", {}).get("score"),
            "earn": cats.get("earnings", {}).get("score"),
            "tech": cats.get("technicals", {}).get("score"),
            "exp":  cats.get("experts", {}).get("score"),
            "narrative": rating.get("narrative", ""),
        })
    return pd.DataFrame(rows)
