"""
Alpha Factors — 30 quantitative scoring factors across 4 categories.
All factors return standardized values (mean=0, std=1).
"""
import pandas as pd
import numpy as np


def compute_all_alphas(df: pd.DataFrame) -> dict:
    """
    Compute all alpha factors for a single coin.

    Returns dict with category scores (mean reversion, momentum, volume, volatility).
    Each score is 0-100.
    """
    mr_scores = _mean_reversion_scores(df)
    mom_scores = _momentum_scores(df)
    vol_scores = _volume_scores(df)
    volat_scores = _volatility_scores(df)

    return {
        "mean_reversion": round(np.mean([s for s in mr_scores if s is not None]), 2) if mr_scores else 50,
        "momentum": round(np.mean([s for s in mom_scores if s is not None]), 2) if mom_scores else 50,
        "volume": round(np.mean([s for s in vol_scores if s is not None]), 2) if vol_scores else 50,
        "volatility": round(np.mean([s for s in volat_scores if s is not None]), 2) if volat_scores else 50,
    }


def _mean_reversion_scores(df: pd.DataFrame) -> list[float | None]:
    """10 mean reversion factors. Returns list of 0-100 scores."""
    close = df["close"]
    scores: list[float | None] = []

    if len(close) < 50:
        return [50.0] * 10

    # MR-1: Price vs SMA20
    sma20 = close.rolling(20).mean().iloc[-1]
    mr1 = 50 - ((close.iloc[-1] - sma20) / sma20 * 500)
    scores.append(_clamp(mr1))

    # MR-2: Price vs VWAP
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vwap = (tp * df["volume"]).cumsum() / df["volume"].cumsum()
    mr2 = 50 - ((close.iloc[-1] - vwap.iloc[-1]) / vwap.iloc[-1] * 500)
    scores.append(_clamp(mr2))

    # MR-3: Z-score 50
    z50 = (close.iloc[-1] - close.rolling(50).mean().iloc[-1]) / (close.rolling(50).std().iloc[-1] + 1e-10)
    scores.append(_clamp(50 - z50 * 10))

    # MR-4: RSI mean reversion
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta).where(delta < 0, 0).rolling(14).mean()
    rs = gain / (loss + 1e-10)
    rsi = 100 - (100 / (1 + rs)).iloc[-1]
    # Oversold = bullish MR signal
    scores.append(_clamp(100 - rsi))

    # MR-5: BB position
    sma = close.rolling(20).mean().iloc[-1]
    std = close.rolling(20).std().iloc[-1]
    upper = sma + 2 * std
    lower = sma - 2 * std
    bb_pos = (close.iloc[-1] - lower) / (upper - lower + 1e-10)
    scores.append(_clamp((1 - bb_pos) * 100))  # Lower = more MR potential

    # MR-6: Keltner Channel
    ema20 = close.ewm(span=20).mean().iloc[-1]
    atr14 = _calc_atr(df, 14)
    kc_upper = ema20 + 2 * atr14
    kc_lower = ema20 - 2 * atr14
    kc_pos = (close.iloc[-1] - kc_lower) / (kc_upper - kc_lower + 1e-10)
    scores.append(_clamp((1 - kc_pos) * 100))

    # MR-7: Double SMA (5 and 20)
    sma5 = close.rolling(5).mean().iloc[-1]
    mr7 = 50 - ((close.iloc[-1] - sma5) / sma5 * 300)
    scores.append(_clamp(mr7))

    # MR-8: Price deviation from 10-period mean
    mean10 = close.rolling(10).mean().iloc[-1]
    std10 = close.rolling(10).std().iloc[-1]
    dev10 = (close.iloc[-1] - mean10) / (std10 + 1e-10)
    scores.append(_clamp(50 - dev10 * 15))

    # MR-9: ROC mean reversion
    roc = close.pct_change(10).iloc[-1] * 100
    scores.append(_clamp(50 - roc * 5))

    # MR-10: Stochastic-like
    low14 = close.rolling(14).min().iloc[-1]
    high14 = close.rolling(14).max().iloc[-1]
    stoch = (close.iloc[-1] - low14) / (high14 - low14 + 1e-10) * 100
    scores.append(_clamp(100 - stoch))

    return scores


def _momentum_scores(df: pd.DataFrame) -> list[float | None]:
    """8 momentum factors."""
    close = df["close"]
    scores: list[float | None] = []

    if len(close) < 30:
        return [50.0] * 8

    # MOM-1: 20-period momentum
    mom20 = close.pct_change(20).iloc[-1] * 100
    scores.append(_clamp(50 + mom20 * 10))

    # MOM-2: 10-period momentum
    mom10 = close.pct_change(10).iloc[-1] * 100
    scores.append(_clamp(50 + mom10 * 10))

    # MOM-3: Time-series rank (20) - vectorized version
    rolling_mean = close.shift(1).rolling(19).mean()
    rank = (close.iloc[-1] > rolling_mean).astype(float) * 100
    scores.append(_clamp(float(rank.iloc[-1])))

    # MOM-4: Normalized position in 20-day range
    high20 = close.rolling(20).max().iloc[-1]
    low20 = close.rolling(20).min().iloc[-1]
    norm_pos = (close.iloc[-1] - low20) / (high20 - low20 + 1e-10) * 100
    scores.append(_clamp(norm_pos))

    # MOM-5: MACD histogram sign
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9).mean()
    hist = (macd - signal).iloc[-1]
    scores.append(_clamp(50 + hist / close.iloc[-1] * 10000))

    # MOM-6: EMA alignment
    ema20 = close.ewm(span=20).mean().iloc[-1]
    ema50 = close.ewm(span=50).mean().iloc[-1] if len(close) >= 50 else ema20
    if close.iloc[-1] > ema20 > ema50:
        scores.append(80)
    elif close.iloc[-1] < ema20 < ema50:
        scores.append(20)
    else:
        scores.append(50)

    # MOM-7: Rate of change acceleration
    roc5 = close.pct_change(5).iloc[-1]
    roc10 = close.pct_change(10).iloc[-1] if len(close) >= 10 else roc5
    accel = (roc5 - roc10) * 100
    scores.append(_clamp(50 + accel * 20))

    # MOM-8: Consecutive green candles
    recent = close.iloc[-5:]
    green = sum(1 for i in range(1, len(recent)) if recent.iloc[i] > recent.iloc[i-1])
    scores.append(_clamp(green / 4 * 100))

    return scores


def _volume_scores(df: pd.DataFrame) -> list[float | None]:
    """6 volume factors."""
    close, volume = df["close"], df["volume"]
    scores: list[float | None] = []

    if len(close) < 20:
        return [50.0] * 6

    # VOL-1: Volume ratio vs 20-period avg
    vol_ratio = volume.iloc[-1] / volume.rolling(20).mean().iloc[-1]
    scores.append(_clamp(vol_ratio * 50))

    # VOL-2: Volume-price trend (OBV-like)
    direction = np.sign(close.diff())
    obv = (direction * volume).cumsum()
    obv_mom = obv.pct_change(10).iloc[-1] * 100
    scores.append(_clamp(50 + obv_mom * 5))

    # VOL-3: Volume spike detection
    vol_spike = vol_ratio > 2.0
    price_up = close.iloc[-1] > close.iloc[-2]
    if vol_spike and price_up:
        scores.append(85)
    elif vol_spike and not price_up:
        scores.append(25)
    else:
        scores.append(50)

    # VOL-4: Accumulation/Distribution
    clv = ((close - df["low"]) - (df["high"] - close)) / (df["high"] - df["low"] + 1e-10)
    ad = (clv * volume).cumsum()
    ad_mom = ad.pct_change(10).iloc[-1] * 100
    scores.append(_clamp(50 + ad_mom * 5))

    # VOL-5: Volume trend (increasing/decreasing)
    recent_vols = volume.iloc[-5:].values
    if recent_vols[-1] > recent_vols[0] * 1.2:
        scores.append(70)
    elif recent_vols[-1] < recent_vols[0] * 0.8:
        scores.append(35)
    else:
        scores.append(50)

    # VOL-6: Volume-weighted price momentum
    vwap = ((df["high"] + df["low"] + close) / 3 * volume).cumsum() / volume.cumsum()
    vwap_mom = (close.iloc[-1] - vwap.iloc[-1]) / vwap.iloc[-1] * 100
    scores.append(_clamp(50 + vwap_mom * 20))

    return scores


def _volatility_scores(df: pd.DataFrame) -> list[float | None]:
    """6 volatility factors."""
    close = df["close"]
    scores: list[float | None] = []

    if len(close) < 20:
        return [50.0] * 6

    # VOLAT-1: ATR as % of price (sweet spot 0.5-3%)
    atr = _calc_atr(df, 14)
    atr_pct = (atr / close.iloc[-1]) * 100
    if 0.5 <= atr_pct <= 3.0:
        scores.append(80)
    elif atr_pct > 5.0:
        scores.append(20)  # Too volatile
    elif atr_pct >= 0.2:
        scores.append(_clamp(atr_pct / 3.0 * 80))
    else:
        scores.append(30)  # Too quiet

    # VOLAT-2: Volatility regime
    returns = close.pct_change()
    vol_20 = returns.rolling(20).std().iloc[-1]
    vol_60 = returns.rolling(60).std().iloc[-1] if len(close) >= 60 else vol_20
    vol_ratio = vol_20 / vol_60 if vol_60 > 0 else 1.0
    if 0.7 <= vol_ratio <= 1.3:
        scores.append(70)  # Stable vol
    elif vol_ratio > 2.0:
        scores.append(20)  # Vol spike — risky
    else:
        scores.append(50)

    # VOLAT-3: Daily range
    daily_range = (df["high"].iloc[-1] - df["low"].iloc[-1]) / close.iloc[-1] * 100
    if 1.0 <= daily_range <= 5.0:
        scores.append(75)
    elif daily_range > 8.0:
        scores.append(25)
    else:
        scores.append(50)

    # VOLAT-4: Bollinger Band squeeze (breakout potential)
    sma20 = close.rolling(20).mean().iloc[-1]
    std20 = close.rolling(20).std().iloc[-1]
    bb_width = (2 * 2 * std20) / sma20 * 100
    bb_width_prev = (2 * 2 * close.rolling(20).std().iloc[-5]) / close.rolling(20).mean().iloc[-5] * 100
    if bb_width < bb_width_prev * 0.9:
        scores.append(75)  # Squeeze — breakout imminent
    else:
        scores.append(50)

    # VOLAT-5: Return consistency (lower variance = higher score for stability)
    ret_10 = close.pct_change().iloc[-10:]
    consistency = 1 - (ret_10.std() + 1e-10)
    scores.append(_clamp(consistency * 100))

    # VOLAT-6: Price stability (smooth trend preferred)
    ret_std = close.pct_change().rolling(20).std().iloc[-1]
    if ret_std < 0.01:
        scores.append(80)
    elif ret_std < 0.02:
        scores.append(60)
    elif ret_std < 0.04:
        scores.append(40)
    else:
        scores.append(20)

    return scores


def _calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Calculate current ATR value."""
    high, low, close = df["high"], df["low"], df["close"]
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    # Use np.maximum.reduce for faster computation
    tr = pd.Series(np.maximum.reduce([tr1.values, tr2.values, tr3.values]), index=df.index)
    atr = tr.rolling(window=period, min_periods=1).mean()
    return float(atr.iloc[-1])


def _clamp(value: float) -> float:
    """Clamp value to 0-100 range."""
    return max(0, min(100, value))
