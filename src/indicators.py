"""
Technical Indicators — RSI, MACD, EMA, ADX, ATR, Bollinger Bands, VWAP, OBV.
All functions accept list of kline dicts or pandas DataFrame.
"""
import pandas as pd
import numpy as np
from typing import Any


def _to_df(candles: list[dict[str, Any]]) -> pd.DataFrame:
    """Convert list of kline dicts to DataFrame."""
    return pd.DataFrame(candles)


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> dict:
    """Relative Strength Index."""
    close = df["close"]
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.rolling(window=period, min_periods=1).mean()
    avg_loss = loss.rolling(window=period, min_periods=1).mean()

    rs = avg_gain / avg_loss.replace(0, float("inf"))
    rsi = 100 - (100 / (1 + rs))
    current = round(float(rsi.iloc[-1]), 2)

    if current >= 70:
        signal, bias = "overbought", "bearish"
    elif current <= 30:
        signal, bias = "oversold", "bullish"
    elif current >= 50:
        signal, bias = "bullish", "bullish"
    else:
        signal, bias = "bearish", "bearish"

    return {"value": current, "signal": signal, "bias": bias}


def calculate_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26,
                   signal_period: int = 9) -> dict:
    """MACD — Moving Average Convergence Divergence."""
    close = df["close"]
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
    histogram = macd_line - signal_line

    current_macd = round(float(macd_line.iloc[-1]), 6)
    current_signal = round(float(signal_line.iloc[-1]), 6)
    current_hist = round(float(histogram.iloc[-1]), 6)

    # Cross detection
    bullish_cross = (float(macd_line.iloc[-2]) <= float(signal_line.iloc[-2]) and
                     current_macd > current_signal)
    bearish_cross = (float(macd_line.iloc[-2]) >= float(signal_line.iloc[-2]) and
                     current_macd < current_signal)

    if bullish_cross:
        status = "bullish_cross"
        bias = "bullish"
    elif bearish_cross:
        status = "bearish_cross"
        bias = "bearish"
    elif current_macd > current_signal:
        status = "above_signal"
        bias = "bullish"
    else:
        status = "below_signal"
        bias = "bearish"

    return {
        "macd": current_macd, "signal": current_signal,
        "histogram": current_hist, "status": status, "bias": bias
    }


def calculate_ema(df: pd.DataFrame, periods: list[int] = [20, 50, 200]) -> dict:
    """Multiple EMAs with alignment check."""
    close = df["close"]
    current_price = float(close.iloc[-1])
    emas = {}

    for p in periods:
        if len(close) >= p:
            emas[f"ema_{p}"] = round(float(close.ewm(span=p, adjust=False).mean().iloc[-1]), 4)

    # Alignment: price > ema20 > ema50 > ema200 = bullish
    if all(k in emas for k in ["ema_20", "ema_50", "ema_200"]):
        if current_price > emas["ema_20"] > emas["ema_50"] > emas["ema_200"]:
            alignment = "bullish_aligned"
        elif current_price < emas["ema_20"] < emas["ema_50"] < emas["ema_200"]:
            alignment = "bearish_aligned"
        else:
            alignment = "mixed"
    else:
        alignment = "insufficient_data"

    return {"price": current_price, "emas": emas, "alignment": alignment}


def calculate_adx(df: pd.DataFrame, period: int = 14) -> dict:
    """Average Directional Index — trend strength."""
    high, low, close = df["high"], df["low"], df["close"]

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period, min_periods=1).mean()

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

    plus_di = 100 * (plus_dm.rolling(window=period, min_periods=1).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(window=period, min_periods=1).mean() / atr)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.rolling(window=period, min_periods=1).mean()

    current_adx = round(float(adx.iloc[-1]), 2)
    current_plus_di = round(float(plus_di.iloc[-1]), 2)
    current_minus_di = round(float(minus_di.iloc[-1]), 2)

    if current_adx >= 50:
        strength = "very_strong"
    elif current_adx >= 25:
        strength = "strong"
    elif current_adx >= 20:
        strength = "moderate"
    else:
        strength = "weak"

    direction = "bullish" if current_plus_di > current_minus_di else "bearish"

    return {
        "adx": current_adx, "plus_di": current_plus_di,
        "minus_di": current_minus_di, "trend_strength": strength,
        "direction": direction
    }


def calculate_atr(df: pd.DataFrame, period: int = 14) -> dict:
    """Average True Range — volatility measure."""
    high, low, close = df["high"], df["low"], df["close"]

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period, min_periods=1).mean()

    current_atr = round(float(atr.iloc[-1]), 4)
    current_price = float(close.iloc[-1])
    atr_pct = round((current_atr / current_price) * 100, 2) if current_price > 0 else 0

    return {"atr": current_atr, "atr_pct": atr_pct, "price": current_price}


def calculate_bollinger_bands(df: pd.DataFrame, period: int = 20,
                               std_dev: float = 2.0) -> dict:
    """Bollinger Bands — volatility bands."""
    close = df["close"]
    sma = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()

    upper = sma + std_dev * std
    lower = sma - std_dev * std
    current_price = float(close.iloc[-1])

    # Position within bands (0 = lower, 1 = upper)
    band_width = float(upper.iloc[-1] - lower.iloc[-1])
    if band_width > 0:
        position = (current_price - float(lower.iloc[-1])) / band_width
    else:
        position = 0.5

    # %B indicator
    pct_b = round(position, 3)

    # Squeeze detection (narrow bands)
    sma_prev = sma.iloc[-5]
    std_prev = std.iloc[-5]
    width_now = float((upper.iloc[-1] - lower.iloc[-1]) / sma.iloc[-1] * 100)
    width_prev = float((sma_prev + std_dev * std_prev - (sma_prev - std_dev * std_prev)) / sma_prev * 100)
    squeeze = width_now < width_prev * 0.9  # 10% contraction

    return {
        "upper": round(float(upper.iloc[-1]), 4),
        "middle": round(float(sma.iloc[-1]), 4),
        "lower": round(float(lower.iloc[-1]), 4),
        "pct_b": pct_b,
        "width_pct": round(width_now, 2),
        "squeeze": squeeze,
        "price": current_price
    }


def calculate_vwap(df: pd.DataFrame) -> dict:
    """Volume Weighted Average Price."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    vwap = (typical_price * df["volume"]).cumsum() / df["volume"].cumsum()
    current_vwap = float(vwap.iloc[-1])
    current_price = float(df["close"].iloc[-1])

    deviation_pct = ((current_price - current_vwap) / current_vwap) * 100

    return {
        "vwap": round(current_vwap, 4),
        "price": current_price,
        "deviation_pct": round(deviation_pct, 2),
        "bias": "bullish" if current_price > current_vwap else "bearish"
    }


def calculate_obv(df: pd.DataFrame) -> dict:
    """On-Balance Volume — volume flow indicator."""
    direction = np.sign(df["close"].diff())
    obv = (direction * df["volume"]).cumsum()

    # OBV momentum (rate of change over 10 periods)
    obv_mom = obv.pct_change(10)
    current_mom = float(obv_mom.iloc[-1]) if not pd.isna(obv_mom.iloc[-1]) else 0

    # OBV trend
    obv_sma = obv.rolling(20).mean()
    trend = "bullish" if obv.iloc[-1] > float(obv_sma.iloc[-1]) else "bearish"

    return {"momentum": round(current_mom, 4), "trend": trend}


def calculate_volume_analysis(df: pd.DataFrame) -> dict:
    """Volume spike and trend analysis."""
    current_vol = float(df["volume"].iloc[-1])
    avg_vol = float(df["volume"].rolling(20).mean().iloc[-1])
    vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0

    # Volume trend (increasing/decreasing over last 5 candles)
    recent_vols = df["volume"].iloc[-5:].values
    vol_trend = "increasing" if recent_vols[-1] > recent_vols[0] else "decreasing"

    return {
        "current_volume": current_vol,
        "avg_volume": round(avg_vol, 2),
        "volume_ratio": round(vol_ratio, 2),
        "volume_trend": vol_trend,
        "spike": vol_ratio > 2.0
    }
