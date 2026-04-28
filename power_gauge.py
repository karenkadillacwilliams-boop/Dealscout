"""Power Gauge rating — Python port of stock_evaluator/scripts/lib/power-gauge.js.

Faithful 1:1 port of the algorithm calibrated against 10 Chaikin Analytics PDFs
(Apr 2026 baseline: 10/10 overall match, 24/40 category match).

Differences from the JS reference:
- Insider Activity sub-factor returns a neutral 50 (would need EDGAR Form 4 data).
- Industry Rel Strength sub-factor returns a neutral 50 (would need a sector
  ETF bars map).
- Earnings Estimate Trend reuses Earnings Surprise as a fallback (Yahoo's
  /quoteSummary 'earningsTrend' module is unreliable through yfinance).

Everything else (financials, earnings, technicals, LT trend overlay, narrative)
is a direct port of the JS thresholds and weights.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

# ──────────────────────────────────────────────────────────────────────────────
# Constants — must match power-gauge.js
# ──────────────────────────────────────────────────────────────────────────────

LABEL_SCORES = {
    "Very Bullish": 85, "Bullish": 70, "Neutral+": 58, "Neutral": 50,
    "Neutral-": 42, "Bearish": 30, "Very Bearish": 15,
}

LT_EMA_LENGTH = 100

SECTOR_VALUATION_MEDIANS = {
    "Technology":             {"pbMedian": 5.5, "psMedian": 5.5, "pbDistressFloor": 1.0},
    "Communication Services": {"pbMedian": 3.5, "psMedian": 3.0, "pbDistressFloor": 0.8},
    "Consumer Cyclical":      {"pbMedian": 2.8, "psMedian": 1.2, "pbDistressFloor": 0.7},
    "Consumer Defensive":     {"pbMedian": 3.0, "psMedian": 1.3, "pbDistressFloor": 0.8},
    "Healthcare":             {"pbMedian": 4.5, "psMedian": 4.0, "pbDistressFloor": 0.9},
    "Financial Services":     {"pbMedian": 1.2, "psMedian": 2.8, "pbDistressFloor": 0.5},
    "Industrials":            {"pbMedian": 3.2, "psMedian": 1.6, "pbDistressFloor": 0.8},
    "Energy":                 {"pbMedian": 1.8, "psMedian": 1.2, "pbDistressFloor": 0.7},
    "Utilities":              {"pbMedian": 1.8, "psMedian": 2.2, "pbDistressFloor": 0.9},
    "Real Estate":            {"pbMedian": 2.2, "psMedian": 6.0, "pbDistressFloor": 0.7},
    "Basic Materials":        {"pbMedian": 2.0, "psMedian": 1.4, "pbDistressFloor": 0.6},
}
FALLBACK_VALUATION_MEDIANS = {"pbMedian": 3.5, "psMedian": 3.0, "pbDistressFloor": 0.8}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def clamp(n: float, lo: float = 0, hi: float = 100) -> int:
    return int(round(max(lo, min(hi, n))))


def _get(d: dict | None, key: str, default=None):
    if not d:
        return default
    v = d.get(key, default)
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return default
    return v


def _score_relative_to_median(value, median, distress_floor=None, apply_distress=True) -> int:
    if value is None or median is None or median <= 0:
        return 50
    if apply_distress and distress_floor is not None and value < distress_floor:
        return 20 if value < distress_floor * 0.5 else 35
    rel = value / median
    if rel <= 0.4:  return 90
    if rel <= 0.7:  return 80
    if rel <= 1.0:  return 70
    if rel <= 1.4:  return 58
    if rel <= 2.0:  return 45
    if rel <= 3.0:  return 32
    if rel <= 5.0:  return 20
    if rel <= 10.0: return 10
    return 5


def _sector_medians(fundamentals: dict) -> dict:
    sector = _get(fundamentals, "sector")
    return SECTOR_VALUATION_MEDIANS.get(sector, FALLBACK_VALUATION_MEDIANS)


def _bucket_base(score: float) -> str:
    if score >= 75: return "Very Bullish"
    if score >= 61: return "Bullish"
    if score >= 45: return "Neutral"
    if score >= 28: return "Bearish"
    return "Very Bearish"


def _label_from_score(score: float) -> str:
    if score >= 75: return "Very Bullish"
    if score >= 60: return "Bullish"
    if score >= 45: return "Neutral"
    if score >= 30: return "Bearish"
    return "Very Bearish"


def _ema(values: list[float], length: int) -> float | None:
    if len(values) < length:
        return None
    k = 2.0 / (length + 1)
    ema = float(np.mean(values[:length]))
    for v in values[length:]:
        ema = v * k + ema * (1 - k)
    return ema


# ──────────────────────────────────────────────────────────────────────────────
# Sub-factor scorers — Financials
# ──────────────────────────────────────────────────────────────────────────────

def score_lt_debt_to_equity(f: dict) -> dict:
    de = _get(f, "debtToEquity")
    if de is None:
        return {"score": 35, "detail": "no D/E data (penalized)"}
    ratio = de / 100 if de > 10 else de
    if   ratio < 0.15: s = 90
    elif ratio < 0.35: s = 75
    elif ratio < 0.60: s = 60
    elif ratio < 1.00: s = 45
    elif ratio < 2.00: s = 25
    else:              s = 10
    return {"score": s, "detail": f"D/E {ratio:.2f}"}


def score_price_to_book(f: dict) -> dict:
    pb = _get(f, "priceToBook")
    if pb is None:
        return {"score": 35, "detail": "no P/B data (penalized)"}
    m = _sector_medians(f)
    s = _score_relative_to_median(pb, m["pbMedian"], m["pbDistressFloor"], True)
    return {"score": s, "detail": f"P/B {pb:.2f} vs {_get(f, 'sector', 'Unknown')} median {m['pbMedian']:.1f}"}


def score_return_on_equity(f: dict) -> dict:
    roe = _get(f, "returnOnEquity")
    if roe is None:
        return {"score": 35, "detail": "no ROE data (penalized)"}
    if   roe > 0.30:  s = 90
    elif roe > 0.20:  s = 80
    elif roe > 0.12:  s = 65
    elif roe > 0.05:  s = 50
    elif roe > 0:     s = 35
    elif roe > -0.10: s = 25
    else:             s = 10
    return {"score": s, "detail": f"ROE {roe * 100:.1f}%"}


def score_price_to_sales(f: dict) -> dict:
    ps = _get(f, "priceToSalesTrailing12Months")
    if ps is None:
        return {"score": 35, "detail": "no P/S data (penalized)"}
    m = _sector_medians(f)
    s = _score_relative_to_median(ps, m["psMedian"], None, False)
    return {"score": s, "detail": f"P/S {ps:.1f} vs {_get(f, 'sector', 'Unknown')} median {m['psMedian']:.1f}"}


def score_fcf_quality(f: dict) -> dict:
    score = 50
    bits = []
    fcf = _get(f, "freeCashflow")
    mcap = _get(f, "marketCap")
    rev = _get(f, "totalRevenue")
    if fcf is not None and mcap and mcap > 0:
        y = fcf / mcap
        if   y >= 0.05: score += 20; bits.append(f"yield {y*100:.1f}% (strong)")
        elif y >= 0.02: score += 10; bits.append(f"yield {y*100:.1f}%")
        elif y < 0:     score -= 15; bits.append(f"yield {y*100:.1f}% (negative)")
        else:                       bits.append(f"yield {y*100:.1f}%")
    if fcf is not None and rev and rev > 0:
        m_pct = (fcf / rev) * 100
        if   m_pct > 15: score += 10; bits.append(f"margin {m_pct:.1f}%")
        elif m_pct > 8:  score += 5;  bits.append(f"margin {m_pct:.1f}%")
        elif m_pct < 0:  score -= 10; bits.append(f"margin {m_pct:.1f}% (negative)")
        else:                         bits.append(f"margin {m_pct:.1f}%")
    return {"score": clamp(score), "detail": "; ".join(bits) or "no FCF data"}


def score_financials_category(f: dict) -> dict:
    factors = {
        "LT Debt / Equity": score_lt_debt_to_equity(f),
        "Price / Book":     score_price_to_book(f),
        "Return on Equity": score_return_on_equity(f),
        "Price / Sales":    score_price_to_sales(f),
        "Free Cash Flow":   score_fcf_quality(f),
    }
    mean = sum(v["score"] for v in factors.values()) / 5
    return {"score": int(round(mean)), "factors": factors}


# ──────────────────────────────────────────────────────────────────────────────
# Sub-factor scorers — Earnings
# ──────────────────────────────────────────────────────────────────────────────

def score_pg_earnings_growth(f: dict) -> dict:
    hist = f.get("earningsHistory") or []
    valid = [h for h in hist if h.get("actual") is not None and math.isfinite(h["actual"])]
    if len(valid) < 2:
        # Fall back: use Yahoo's MRQ growth field through scoreEarningsTrend's logic
        return _earnings_growth_fallback(f)

    avg = sum(h["actual"] for h in valid) / len(valid)
    no_negatives = all(h["actual"] >= 0 for h in valid)
    all_negative = not any(h["actual"] > 0 for h in valid)

    if no_negatives:
        if   avg > 1.5:  s = 80
        elif avg > 0.8:  s = 72
        elif avg > 0.3:  s = 62
        elif avg > 0.05: s = 58
        else:            s = 50
    elif all_negative:
        if   avg < -0.5: s = 12
        elif avg < -0.2: s = 22
        else:            s = 30
    else:
        s = 40

    beats = sum(1 for h in valid if (h.get("surprisePct") or 0) > 0)
    beat_rate = beats / len(valid) if valid else 0
    if   beat_rate >= 0.75: s += 10
    elif beat_rate >= 0.50: s += 4

    g = _get(f, "earningsGrowth")
    if g is not None:
        if   g >  0.50: s += 15
        elif g >  0.25: s += 10
        elif g >  0.10: s += 5
        elif g < -0.30: s -= 10
        elif g < -0.15: s -= 5

    return {"score": clamp(s), "detail": f"n={len(valid)}, avg=${avg:.2f}, beats={beats}/{len(valid)}"}


def _earnings_growth_fallback(f: dict) -> dict:
    g = _get(f, "earningsGrowth")
    if g is None:
        return {"score": 50, "detail": "no earnings growth data"}
    if   g >= 0.50: s = 80
    elif g >= 0.20: s = 65
    elif g >= 0.05: s = 55
    elif g <  -0.10: s = 30
    else:           s = 50
    return {"score": clamp(s), "detail": f"MRQ YoY {g*100:.1f}%"}


def score_earnings_surprises(f: dict) -> dict:
    hist = f.get("earningsHistory") or []
    if not hist:
        # No EPS history — but revenue/guidance inputs from the triple-play
        # pipeline may still carry signal, so honour them if present before
        # returning neutral.
        rev_surp = f.get("revenueSurprisePct")
        gdelta = f.get("bullishShareDelta")
        if rev_surp is None and gdelta is None:
            return {"score": 50, "detail": "no earnings history"}
        score = 50
        extras: list[str] = []
        if rev_surp is not None:
            if   rev_surp > 10: score += 15
            elif rev_surp > 5:  score += 10
            elif rev_surp > 2:  score += 5
            elif rev_surp < -5: score -= 8
            extras.append(f"rev {rev_surp:+.1f}%")
        if gdelta is not None:
            if   gdelta > 5:  score += 10
            elif gdelta > 2:  score += 6
            elif gdelta < -2: score -= 6
            extras.append(f"gdn {gdelta:+.1f}pp")
        detail = "no EPS history"
        if extras:
            detail += f" ({', '.join(extras)})"
        return {"score": clamp(score), "detail": detail}

    score = 50
    streak = 0
    for q in hist:
        sp = q.get("surprisePct")
        if sp is not None and sp > 0:
            streak += 1
        else:
            break
    if   streak >= 4: score += 25
    elif streak >= 3: score += 18
    elif streak >= 2: score += 12
    elif streak >= 1: score += 5

    surprises = [abs(q["surprisePct"]) for q in hist if q.get("surprisePct") is not None]
    avg_sur = sum(surprises) / len(surprises) if surprises else 0
    if   avg_sur > 20: score += 15
    elif avg_sur > 10: score += 10
    elif avg_sur > 5:  score += 5

    for q in hist:
        sp = q.get("surprisePct")
        if sp is not None and sp < -5:
            score -= 8

    # Revenue surprise booster (additive). Triple-play pipeline supplies this
    # via yfinance earnings_history; may be absent when data is thin.
    rev_surp = f.get("revenueSurprisePct")
    if rev_surp is not None:
        if   rev_surp > 10: score += 15
        elif rev_surp > 5:  score += 10
        elif rev_surp > 2:  score += 5
        elif rev_surp < -5: score -= 8

    # Guidance / analyst momentum delta: (strongBuy+buy share) after earnings
    # minus before, in percentage points. Sourced via triple-play pipeline.
    gdelta = f.get("bullishShareDelta")
    if gdelta is not None:
        if   gdelta > 5:  score += 10
        elif gdelta > 2:  score += 6
        elif gdelta < -2: score -= 6

    extras: list[str] = []
    if rev_surp is not None:
        extras.append(f"rev {rev_surp:+.1f}%")
    if gdelta is not None:
        extras.append(f"gdn {gdelta:+.1f}pp")
    detail = f"{streak}-qtr beat streak, avg surprise {avg_sur:.1f}%"
    if extras:
        detail += f" ({', '.join(extras)})"
    return {"score": clamp(score), "detail": detail}


def score_earnings_trend(f: dict) -> dict:
    g = _get(f, "earningsGrowth")
    if g is not None:
        if   g >  0.30: s = 90
        elif g >  0.15: s = 75
        elif g >  0.05: s = 65
        elif g > -0.05: s = 50
        elif g > -0.15: s = 35
        elif g > -0.30: s = 20
        else:           s = 10
        return {"score": clamp(s), "detail": f"MRQ YoY {g*100:.1f}% (Yahoo)"}
    return {"score": 50, "detail": "insufficient earnings data for trend"}


def score_projected_pe(f: dict) -> dict:
    fpe = _get(f, "forwardPE")
    if fpe is None:
        return {"score": 30, "detail": "no forward P/E (negative earnings)"}
    if fpe <= 0:
        return {"score": 20, "detail": f"fwd P/E {fpe:.1f} (negative)"}
    if   fpe < 10:  s = 85
    elif fpe < 15:  s = 72
    elif fpe < 20:  s = 60
    elif fpe < 30:  s = 48
    elif fpe < 50:  s = 32
    elif fpe < 100: s = 18
    else:           s = 8
    return {"score": s, "detail": f"fwd P/E {fpe:.1f}"}


def score_earnings_consistency(f: dict) -> dict:
    hist = f.get("earningsHistory") or []
    series = [h["actual"] for h in hist if h.get("actual") is not None and math.isfinite(h["actual"])]
    if len(series) < 4:
        return {"score": 50, "detail": "insufficient earnings history"}
    mean = sum(series) / len(series)
    if abs(mean) < 0.001:
        return {"score": 30, "detail": "earnings near zero (unreliable)"}
    var = sum((v - mean) ** 2 for v in series) / len(series)
    cv = math.sqrt(var) / abs(mean)

    sign_flips = 0
    for i in range(1, len(series)):
        if series[i] != 0 and series[i - 1] != 0 and (series[i] > 0) != (series[i - 1] > 0):
            sign_flips += 1

    if   cv < 0.2: s = 90
    elif cv < 0.4: s = 75
    elif cv < 0.7: s = 60
    elif cv < 1.0: s = 45
    elif cv < 1.5: s = 30
    else:          s = 15
    s -= min(sign_flips * 10, 30)
    if all(v > 0 for v in series):
        s += 10
    return {"score": clamp(s), "detail": f"cv={cv:.2f}, flips={sign_flips}, n={len(series)}"}


def score_earnings_category(f: dict) -> dict:
    factors = {
        "Earnings Growth":      score_pg_earnings_growth(f),
        "Earnings Surprise":    score_earnings_surprises(f),
        "Earnings Trend":       score_earnings_trend(f),
        "Projected P/E":        score_projected_pe(f),
        "Earnings Consistency": score_earnings_consistency(f),
    }
    mean = sum(v["score"] for v in factors.values()) / 5
    return {"score": int(round(mean)), "factors": factors}


# ──────────────────────────────────────────────────────────────────────────────
# Sub-factor scorers — Technicals (computed from raw OHLCV)
# ──────────────────────────────────────────────────────────────────────────────

def score_relative_strength_vs_market(closes: pd.Series, market_closes: pd.Series) -> dict:
    """Stock 63-day return rank vs SPY return — score 0-100."""
    n = 63
    if len(closes) < n + 1 or len(market_closes) < n + 1:
        return {"score": 50, "detail": "insufficient bars for RS"}
    stock_ret = float(closes.iloc[-1] / closes.iloc[-n - 1] - 1)
    mkt_ret = float(market_closes.iloc[-1] / market_closes.iloc[-n - 1] - 1)
    spread = (stock_ret - mkt_ret) * 100  # percentage points
    # Map -30pp..+30pp → 5..95 linear
    s = clamp(50 + spread * 1.5)
    return {"score": s, "detail": f"63d RS spread {spread:+.1f}pp"}


def score_money_flow_persistency(prices: pd.DataFrame) -> dict:
    """Chaikin Money Flow over 20 bars → score."""
    if len(prices) < 20:
        return {"score": 50, "detail": "insufficient bars for CMF"}
    h, l, c, v = prices["High"], prices["Low"], prices["Close"], prices["Volume"]
    rng = (h - l).replace(0, np.nan)
    mfm = ((c - l) - (h - c)) / rng
    mfv = mfm * v
    cmf = mfv.tail(20).sum() / v.tail(20).sum()
    if pd.isna(cmf):
        return {"score": 50, "detail": "CMF n/a"}
    # CMF is in [-1, +1]; map to 0-100
    s = clamp(50 + float(cmf) * 100)
    return {"score": s, "detail": f"20d CMF {float(cmf):+.2f}"}


def score_price_strength(closes: pd.Series) -> dict:
    """EMA50 alignment + distance — bullish if price > EMA50 > EMA200."""
    if len(closes) < 200:
        return {"score": 50, "detail": "insufficient bars for EMA200"}
    ema50 = float(closes.ewm(span=50, adjust=False).mean().iloc[-1])
    ema200 = float(closes.ewm(span=200, adjust=False).mean().iloc[-1])
    last = float(closes.iloc[-1])
    score = 50
    if last > ema50: score += 15
    if ema50 > ema200: score += 15
    dist50 = (last / ema50 - 1) * 100
    score += clamp(dist50 * 1.5, -20, 20)
    return {"score": clamp(score), "detail": f"px {last:.1f}, EMA50 {ema50:.1f}, EMA200 {ema200:.1f}"}


def score_price_trend_roc(closes: pd.Series) -> dict:
    """5-day momentum + EMA50 distance blend."""
    if len(closes) < 51:
        return {"score": 50, "detail": "insufficient bars for ROC"}
    five_day = (float(closes.iloc[-1]) / float(closes.iloc[-6]) - 1) * 100
    ema50 = float(closes.ewm(span=50, adjust=False).mean().iloc[-1])
    dist50 = (float(closes.iloc[-1]) / ema50 - 1) * 100
    blended = 50 + five_day * 3 + dist50 * 1.5
    return {"score": clamp(blended), "detail": f"5dm {five_day:.1f}%, distEMA50 {dist50:.1f}%"}


def score_volume_trend(prices: pd.DataFrame) -> dict:
    """Recent 5d avg volume vs 20d avg — rising volume = bullish."""
    v = prices["Volume"]
    if len(v) < 25:
        return {"score": 50, "detail": "insufficient volume bars"}
    recent = float(v.tail(5).mean())
    base = float(v.tail(25).head(20).mean())
    if base <= 0:
        return {"score": 50, "detail": "no base volume"}
    ratio = recent / base
    if   ratio > 1.5: s = 85
    elif ratio > 1.2: s = 70
    elif ratio > 0.9: s = 55
    elif ratio > 0.7: s = 40
    else:             s = 25
    return {"score": s, "detail": f"5d/20d vol {ratio:.2f}x"}


def score_technicals_category(prices: pd.DataFrame, market_closes: pd.Series) -> dict:
    closes = prices["Close"]
    factors = {
        "Relative Strength vs Market": score_relative_strength_vs_market(closes, market_closes),
        "Money Flow Persistency":       score_money_flow_persistency(prices),
        "Price Strength":               score_price_strength(closes),
        "Price Trend ROC":              score_price_trend_roc(closes),
        "Volume Trend":                 score_volume_trend(prices),
    }
    mean = sum(v["score"] for v in factors.values()) / 5
    return {"score": int(round(mean)), "factors": factors}


# ──────────────────────────────────────────────────────────────────────────────
# Sub-factor scorers — Experts
# ──────────────────────────────────────────────────────────────────────────────

def score_short_interest(f: dict) -> dict:
    sp = _get(f, "shortPercentOfFloat")
    if sp is None:
        return {"score": 50, "detail": "no short data"}
    pct = sp * 100
    if   pct < 2:  s = 85
    elif pct < 4:  s = 72
    elif pct < 7:  s = 55
    elif pct < 12: s = 38
    elif pct < 20: s = 22
    else:          s = 10
    return {"score": s, "detail": f"{pct:.1f}% of float"}


def score_analyst_rating_trend(f: dict) -> dict:
    rm = _get(f, "recommendationMean")
    if rm is None:
        return {"score": 50, "detail": "no analyst consensus"}
    s = clamp(100 - (rm - 1) * 22.5)
    label = _get(f, "recommendationKey", "")
    return {"score": s, "detail": f"{label} ({rm:.1f})"}


def score_insider_activity_stub(_f: dict) -> dict:
    return {"score": 50, "detail": "insider data not wired (Phase 2 stub)"}


def score_industry_rs_stub(_f: dict) -> dict:
    return {"score": 50, "detail": "sector ETF momentum not wired (Phase 2 stub)"}


def score_earnings_estimate_trend(f: dict) -> dict:
    # Yahoo's revisions module is unreliable through yfinance; fall back to
    # the surprise streak as a directional proxy.
    surprise = score_earnings_surprises(f)
    return {"score": surprise["score"], "detail": "via earnings surprise (revisions n/a)"}


def score_experts_category(f: dict) -> dict:
    factors = {
        "Earnings Estimate Trend": score_earnings_estimate_trend(f),
        "Short Interest":          score_short_interest(f),
        "Insider Activity":        score_insider_activity_stub(f),
        "Analyst Rating Trend":    score_analyst_rating_trend(f),
        "Industry Rel Strength":   score_industry_rs_stub(f),
    }
    mean = sum(v["score"] for v in factors.values()) / 5
    return {"score": int(round(mean)), "factors": factors}


# ──────────────────────────────────────────────────────────────────────────────
# LT Trend Overlay
# ──────────────────────────────────────────────────────────────────────────────

def apply_lt_trend_overlay(base: float, price: float, lt_trend: float | None,
                            cat_scores: dict[str, int]) -> dict:
    base_label = _bucket_base(base)
    if lt_trend is None or price is None:
        return {"label": base_label, "adjustment": "no-trend-data", "trendDelta": None}

    trend_delta = (price - lt_trend) / lt_trend
    price_above = trend_delta > 0.01
    price_below = trend_delta < -0.01

    neg_cat = sum(1 for s in cat_scores.values() if s < 50)
    pos_cat = sum(1 for s in cat_scores.values() if s >= 60)

    if base_label in ("Bullish", "Very Bullish") and price_below:
        return {"label": "Neutral+", "adjustment": "capped-by-trend", "trendDelta": trend_delta}

    if base_label in ("Bearish", "Very Bearish") and price_above:
        return {"label": "Neutral-", "adjustment": "floored-by-trend", "trendDelta": trend_delta}

    extreme_trend = trend_delta > 0.40
    if base_label == "Neutral" and price_above and (pos_cat >= 2 or extreme_trend):
        return {"label": "Bullish", "adjustment": "promoted-by-trend", "trendDelta": trend_delta}
    if base_label == "Neutral" and price_above and pos_cat <= 1 and neg_cat >= 3:
        return {"label": "Neutral-", "adjustment": "floored-by-weakness", "trendDelta": trend_delta}

    if base_label == "Bullish" and price_above and base >= 60 and neg_cat == 0:
        return {"label": "Very Bullish", "adjustment": "upgraded-by-trend", "trendDelta": trend_delta}

    if base_label == "Bearish" and trend_delta < -0.15:
        return {"label": "Very Bearish", "adjustment": "deep-breakdown", "trendDelta": trend_delta}

    return {"label": base_label, "adjustment": "unchanged", "trendDelta": trend_delta}


# ──────────────────────────────────────────────────────────────────────────────
# Narrative
# ──────────────────────────────────────────────────────────────────────────────

_CAT_STRENGTH = {
    "financials": "attractive financial metrics",
    "earnings": "strong earnings performance",
    "technicals": "strong price/volume activity",
    "experts": "positive expert activity",
}
_CAT_WEAKNESS = {
    "financials": "weak financial metrics",
    "earnings": "weak earnings performance",
    "technicals": "weak price/volume activity",
    "experts": "negative expert activity",
}


def generate_narrative(rating: dict, symbol: str) -> str:
    cats = rating["categories"]
    ordered = sorted(
        ((k, v["score"]) for k, v in cats.items()),
        key=lambda kv: kv[1], reverse=True,
    )
    strongest = [(k, s) for k, s in ordered if s >= 60]
    weakest = [(k, s) for k, s in ordered if s < 40]
    label = rating["overall"]["label"]

    parts = [f"The Power Gauge rating for {symbol} is {label}"]
    if len(strongest) >= 2:
        parts.append(f" due to {_CAT_STRENGTH[strongest[0][0]]} and {_CAT_STRENGTH[strongest[1][0]]}.")
    elif strongest:
        parts.append(f" due to {_CAT_STRENGTH[strongest[0][0]]}.")
    else:
        parts.append(".")
    if weakest:
        parts.append(f" The stock has {_CAT_WEAKNESS[weakest[0][0]]}.")

    adj = rating["overall"].get("adjustment")
    if adj == "capped-by-trend":
        parts.append(f" Despite bullish factors, {symbol} has moved below its long-term trend, resulting in a Power Gauge rating of {label}.")
    elif adj == "floored-by-trend":
        parts.append(f" Despite bearish factors, {symbol} has moved above its long-term trend, resulting in a Power Gauge rating of {label}.")
    return "".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Main entry
# ──────────────────────────────────────────────────────────────────────────────

def calculate_power_gauge(symbol: str, prices: pd.DataFrame, fundamentals: dict | None,
                          market_closes: pd.Series) -> dict:
    """Compute a full Power Gauge rating for one stock.

    Args:
        symbol: ticker symbol.
        prices: DataFrame with columns Open/High/Low/Close/Volume, indexed by date.
        fundamentals: dict shaped like Yahoo .info plus 'earningsHistory' list.
        market_closes: Series of SPY closing prices over the same window.
    """
    if prices is None or len(prices) < 30:
        return {
            "symbol": symbol,
            "overall": {"label": "Neutral", "score": 50, "adjustment": "insufficient-data"},
            "categories": None,
            "narrative": "Insufficient data for Power Gauge rating.",
            "ltTrend": None,
        }

    fundamentals = fundamentals or {}
    financials  = score_financials_category(fundamentals)
    earnings    = score_earnings_category(fundamentals)
    technicals  = score_technicals_category(prices, market_closes)
    experts     = score_experts_category(fundamentals)

    base = (financials["score"] + earnings["score"] + technicals["score"] + experts["score"]) / 4

    closes = prices["Close"].astype(float).tolist()
    lt_trend = _ema(closes, LT_EMA_LENGTH)
    current_price = float(prices["Close"].iloc[-1])

    cat_scores = {
        "financials": financials["score"], "earnings": earnings["score"],
        "technicals": technicals["score"], "experts":   experts["score"],
    }
    overlay = apply_lt_trend_overlay(base, current_price, lt_trend, cat_scores)

    rating = {
        "symbol": symbol,
        "overall": {
            "label": overlay["label"],
            "score": int(round(base)),
            "adjustment": overlay["adjustment"],
            "trendDelta": overlay["trendDelta"],
        },
        "categories": {
            "financials": financials, "earnings": earnings,
            "technicals": technicals, "experts": experts,
        },
        "ltTrend": lt_trend,
        "currentPrice": current_price,
    }
    rating["narrative"] = generate_narrative(rating, symbol)
    return rating
