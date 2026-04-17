"""
Pattern Detection — Triangle, Flag, Double Top/Bottom, Breakout.
Returns detected patterns with confidence and direction.
"""
import pandas as pd


def detect_patterns(df: pd.DataFrame) -> list[dict]:
    """Detect common chart patterns. Returns list of pattern dicts."""
    patterns = []
    if len(df) < 15:
        return patterns

    closes = df["close"].values.tolist()
    highs = df["high"].values.tolist()
    lows = df["low"].values.tolist()
    volumes = df["volume"].values.tolist()

    # Check each pattern
    if _check_ascending_triangle(highs, lows):
        patterns.append({
            "name": "Ascending Triangle", "confidence": 0.70,
            "direction": "bullish", "description": "Higher lows, flat resistance"
        })

    if _check_descending_triangle(highs, lows):
        patterns.append({
            "name": "Descending Triangle", "confidence": 0.70,
            "direction": "bearish", "description": "Lower highs, flat support"
        })

    if _check_bullish_flag(closes, volumes):
        patterns.append({
            "name": "Bullish Flag", "confidence": 0.65,
            "direction": "bullish", "description": "Rally + slight pullback, continuation likely"
        })

    if _check_bearish_flag(closes, volumes):
        patterns.append({
            "name": "Bearish Flag", "confidence": 0.65,
            "direction": "bearish", "description": "Drop + slight recovery, continuation likely down"
        })

    if _check_double_bottom(lows, closes):
        patterns.append({
            "name": "Double Bottom", "confidence": 0.75,
            "direction": "bullish", "description": "Two lows at similar level, reversal signal"
        })

    if _check_double_top(highs, closes):
        patterns.append({
            "name": "Double Top", "confidence": 0.75,
            "direction": "bearish", "description": "Two highs at similar level, reversal signal"
        })

    if _check_breakout_resistance(highs, closes, volumes):
        patterns.append({
            "name": "Resistance Breakout", "confidence": 0.70,
            "direction": "bullish", "description": "Price broke above recent resistance with volume"
        })

    if _check_breakdown_support(highs, lows, closes, volumes):
        patterns.append({
            "name": "Support Breakdown", "confidence": 0.70,
            "direction": "bearish", "description": "Price broke below recent support with volume"
        })

    return patterns


def _check_ascending_triangle(highs, lows, window=10) -> bool:
    recent_highs = highs[-window:]
    recent_lows = lows[-window:]
    high_range = (max(recent_highs) - min(recent_highs)) / min(recent_highs)
    if high_range > 0.015:
        return False
    first_half = sum(recent_lows[:window//2]) / (window//2)
    second_half = sum(recent_lows[window//2:]) / (window//2)
    return second_half > first_half * 1.005


def _check_descending_triangle(highs, lows, window=10) -> bool:
    recent_highs = highs[-window:]
    recent_lows = lows[-window:]
    low_range = (max(recent_lows) - min(recent_lows)) / min(recent_lows)
    if low_range > 0.015:
        return False
    first_half = sum(recent_highs[:window//2]) / (window//2)
    second_half = sum(recent_highs[window//2:]) / (window//2)
    return second_half < first_half * 0.995


def _check_bullish_flag(closes, volumes, flag_len=8) -> bool:
    if len(closes) < flag_len + 5:
        return False
    rally_start = closes[-(flag_len + 5)]
    rally_end = closes[-flag_len]
    rally_pct = (rally_end - rally_start) / rally_start
    if rally_pct < 0.03:
        return False
    pullback = (rally_end - closes[-1]) / rally_end
    if pullback > 0.02 or pullback < 0:
        return False
    avg_rally_vol = sum(volumes[-(flag_len+5):-flag_len]) / flag_len
    avg_flag_vol = sum(volumes[-flag_len:]) / flag_len
    return avg_flag_vol < avg_rally_vol * 0.8


def _check_bearish_flag(closes, volumes, flag_len=8) -> bool:
    if len(closes) < flag_len + 5:
        return False
    drop_start = closes[-(flag_len + 5)]
    drop_end = closes[-flag_len]
    drop_pct = (drop_start - drop_end) / drop_start
    if drop_pct < 0.03:
        return False
    recovery = (closes[-1] - drop_end) / drop_end
    if recovery > 0.02 or recovery < 0:
        return False
    avg_drop_vol = sum(volumes[-(flag_len+5):-flag_len]) / flag_len
    avg_flag_vol = sum(volumes[-flag_len:]) / flag_len
    return avg_flag_vol < avg_drop_vol * 0.8


def _check_double_bottom(lows, closes, tolerance=0.01) -> bool:
    if len(lows) < 15:
        return False
    recent_lows = list(lows[-15:])
    min_val = min(recent_lows)
    min_idx = recent_lows.index(min_val)
    for i, lv in enumerate(recent_lows):
        if i == min_idx:
            continue
        if abs(lv - min_val) / min_val < tolerance:
            if float(closes[-1]) > min_val * 1.01:
                return True
    return False


def _check_double_top(highs, closes, tolerance=0.01) -> bool:
    if len(highs) < 15:
        return False
    recent_highs = list(highs[-15:])
    max_val = max(recent_highs)
    max_idx = recent_highs.index(max_val)
    for i, hv in enumerate(recent_highs):
        if i == max_idx:
            continue
        if abs(hv - max_val) / max_val < tolerance:
            if float(closes[-1]) < max_val * 0.99:
                return True
    return False


def _check_breakout_resistance(highs, closes, volumes, lookback=20) -> bool:
    if len(closes) < lookback + 1:
        return False
    recent_highs = highs[-lookback:-1]
    resistance = max(recent_highs)
    current_price = closes[-1]
    current_vol = volumes[-1]
    avg_vol = sum(volumes[-lookback:-1]) / (lookback - 1)
    return current_price > resistance and current_vol > avg_vol * 1.2


def _check_breakdown_support(highs, lows, closes, volumes, lookback=20) -> bool:
    if len(closes) < lookback + 1:
        return False
    recent_lows = lows[-lookback:-1]
    support = min(recent_lows)
    current_price = closes[-1]
    current_vol = volumes[-1]
    avg_vol = sum(volumes[-lookback:-1]) / (lookback - 1)
    return current_price < support and current_vol > avg_vol * 1.2
