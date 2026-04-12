"""Price fetching, period returns, and momentum grading."""
from __future__ import annotations

import pandas as pd
import streamlit as st
import yfinance as yf


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
        return pd.DataFrame(columns=["ticker", "last", "daily_pct", "weekly_pct", "monthly_pct"])

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
        })
    return pd.DataFrame(rows)


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
