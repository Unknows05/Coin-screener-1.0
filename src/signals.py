"""
Signal Generator — Balanced LONG/SHORT/WAIT Logic.
"""
import pandas as pd
import logging

from src.utils import get_price_precision

logger = logging.getLogger(__name__)

def _calc_signal_levels(direction: int, entry: float, atr: float, price_precision: int) -> tuple[float, float]:
    """
    Calculate SL and TP levels for a signal.

    Args:
        direction: 1 for LONG, -1 for SHORT
        entry: Entry price
        atr: Average True Range value
        price_precision: Decimal precision for rounding

    Returns:
        (sl, tp) tuple
    """
    sl_offset = atr * 1.5
    tp_offset = atr * 3.0
    if direction == 1:  # LONG
        return (
            round(entry - sl_offset, price_precision),
            round(entry + tp_offset, price_precision)
        )
    else:  # SHORT
        return (
            round(entry + sl_offset, price_precision),
            round(entry - tp_offset, price_precision)
        )


def generate_signal(coin_data: dict, config: dict) -> dict:
    """
    Generate trading signal with proper LONG/SHORT/WAIT classification.

    Score interpretation:
    - 0-35: STRONG SHORT
    - 35-45: SHORT
    - 45-55: WAIT/NEUTRAL
    - 55-65: LONG
    - 65-100: STRONG LONG
    """
    score = coin_data.get("composite_score", 50)
    tf_metrics = coin_data.get("tf_metrics", {})
    tf_scores = coin_data.get("tf_scores", {})

    regime = coin_data.get("regime", {})
    regime_type = regime.get("regime", "SIDEWAYS") if isinstance(regime, dict) else str(regime)

    # --- Configuration ---
    signal_config = config.get("signal", {})
    long_threshold = signal_config.get("long_min_score", 55)
    short_threshold = signal_config.get("short_min_score", 55)

    # --- Get 15m metrics for breakout detection ---
    m15 = tf_metrics.get("15m", {})
    breakout_bull = m15.get("breakout_bull", False)
    breakout_bear = m15.get("breakout_bear", False)

    # --- Signal Determination ---
    price = coin_data.get("price", 0)
    klines = coin_data.get("klines", [])
    atr = _get_atr(klines) if klines and price > 0 else price * 0.02
    price_prec = get_price_precision(price)

    # Default WAIT
    signal = "WAIT"
    confidence = 50
    entry = price
    sl = None
    tp = None

    # LONG Logic: Score above threshold
    if score >= long_threshold:
        signal = "LONG"
        sl, tp = _calc_signal_levels(1, entry, atr, price_prec)
        confidence = min(98, max(50, int(score)))

    # SHORT Logic: Score below (100 - threshold)
    elif score <= (100 - short_threshold):
        signal = "SHORT"
        sl, tp = _calc_signal_levels(-1, entry, atr, price_prec)
        confidence = min(98, max(50, int(100 - score)))

    # WAIT zone - no trade
    else:
        confidence = int(50 - abs(score - 50))

    # Build Reasons dynamically
    reasons = []
    
    # Breakout reasons
    if breakout_bull and signal == "LONG":
        reasons.append("🔥 Bullish Breakout")
    elif breakout_bear and signal == "SHORT":
        reasons.append("🔻 Bearish Breakout")
    
    # Score-based reasons
    if score >= 75:
        reasons.append("Strong Bullish Momentum")
    elif score >= 60:
        reasons.append("Bullish Momentum")
    elif score <= 25:
        reasons.append("Strong Bearish Momentum")
    elif score <= 40:
        reasons.append("Bearish Momentum")
    
    # Regime alignment
    if signal == "LONG" and regime_type == "BULL":
        reasons.append("Bullish Trend Regime")
    elif signal == "SHORT" and regime_type == "BEAR":
        reasons.append("Bearish Trend Regime")
    elif signal == "WAIT" and regime_type == "SIDEWAYS":
        reasons.append("Sideways Market")
    
    # Volume anomaly
    vol_z = m15.get("vol_z", 0)
    if vol_z > 2.0:
        reasons.append("High Volume Spike")
    elif vol_z < -1.0:
        reasons.append("Low Volume")
    
    # RSI extremes
    rsi = m15.get("rsi", 50)
    if rsi > 70:
        reasons.append("Overbought (RSI)")
    elif rsi < 30:
        reasons.append("Oversold (RSI)")
    
    # Multi-timeframe alignment
    if len(tf_scores) >= 2:
        scores = list(tf_scores.values())
        if all(s >= 60 for s in scores):
            reasons.append("All Timeframes Bullish")
        elif all(s <= 40 for s in scores):
            reasons.append("All Timeframes Bearish")
        elif max(scores) - min(scores) > 30:
            reasons.append("Mixed Timeframe Signals")
    
    return {
        "symbol": coin_data.get("symbol", ""),
        "price": price,
        "signal": signal,
        "confidence": int(confidence),
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "regime": regime_type,
        "score": float(score),
        "reasons": reasons,
        "tf_scores": tf_scores,
        "patterns_detected": coin_data.get("patterns_detected", [])
    }

def _get_atr(klines: list[dict], period: int = 14) -> float:
    import numpy as np
    try:
        df = pd.DataFrame(klines)
        if len(df) == 0:
            return 0.0
        if len(df) < period:
            return float(df["close"].iloc[-1]) * 0.02
        high, low, close = df["high"], df["low"], df["close"]
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        # Use np.maximum.reduce for faster computation
        tr = pd.Series(np.maximum.reduce([tr1.values, tr2.values, tr3.values]), index=df.index)
        return float(tr.rolling(window=period).mean().iloc[-1])
    except Exception:
        return 0.0
