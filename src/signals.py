"""
Signal Generator — Balanced LONG/SHORT/WAIT Logic.

ATR-based dynamic SL/TP per regime:
- BULL LONG: SL 2.5× ATR, TP 3.5× ATR (wide SL for trend)
- SIDEWAYS SHORT: SL 1.5× ATR, TP 2.5× ATR (tight SL for mean-reversion)
- HIGH_VOL: SL 3.0× ATR, TP 4.0× ATR (widest for volatility)
- Default: SL 1.5× ATR, TP 3.0× ATR
"""
import pandas as pd
import logging

from src.utils import get_price_precision

logger = logging.getLogger(__name__)


REGIME_SL_TP = {
    "BULL": {"sl_mult": 2.5, "tp_mult": 3.5},
    "BEAR": {"sl_mult": 2.5, "tp_mult": 3.5},
    "SIDEWAYS": {"sl_mult": 1.5, "tp_mult": 2.5},
    "HIGH_VOL": {"sl_mult": 3.0, "tp_mult": 4.0},
}

DIRECTION_SL_TP = {
    ("SIDEWAYS", "SHORT"): {"sl_mult": 1.5, "tp_mult": 2.5},
    ("SIDEWAYS", "LONG"): {"sl_mult": 2.0, "tp_mult": 2.0},
    ("BULL", "LONG"): {"sl_mult": 2.5, "tp_mult": 3.5},
    ("BULL", "SHORT"): {"sl_mult": 2.0, "tp_mult": 3.0},
    ("BEAR", "SHORT"): {"sl_mult": 2.0, "tp_mult": 3.0},
    ("BEAR", "LONG"): {"sl_mult": 3.0, "tp_mult": 2.5},
    ("HIGH_VOL", "LONG"): {"sl_mult": 3.0, "tp_mult": 4.0},
    ("HIGH_VOL", "SHORT"): {"sl_mult": 3.0, "tp_mult": 4.0},
}


def _get_sl_tp_params(regime: str, signal_type: str) -> tuple[float, float]:
    key = (regime, signal_type)
    if key in DIRECTION_SL_TP:
        cfg = DIRECTION_SL_TP[key]
        return cfg["sl_mult"], cfg["tp_mult"]
    regime_cfg = REGIME_SL_TP.get(regime, {"sl_mult": 1.5, "tp_mult": 3.0})
    return regime_cfg["sl_mult"], regime_cfg["tp_mult"]


def _calc_signal_levels(direction: int, entry: float, atr: float,
                         price_precision: int, regime: str = "SIDEWAYS",
                         signal_type: str = "LONG") -> tuple[float, float]:
    sl_mult, tp_mult = _get_sl_tp_params(regime, signal_type)
    sl_offset = atr * sl_mult
    tp_offset = atr * tp_mult
    if direction == 1:
        return (
            round(entry - sl_offset, price_precision),
            round(entry + tp_offset, price_precision),
        )
    else:
        return (
            round(entry + sl_offset, price_precision),
            round(entry - tp_offset, price_precision),
        )


def generate_signal(coin_data: dict, config: dict) -> dict:
    score = coin_data.get("composite_score", 50)
    tf_metrics = coin_data.get("tf_metrics", {})
    tf_scores = coin_data.get("tf_scores", {})

    regime = coin_data.get("regime", {})
    regime_type = regime.get("regime", "SIDEWAYS") if isinstance(regime, dict) else str(regime)

    signal_config = config.get("signal", {})
    long_threshold = signal_config.get("long_min_score", 55)
    short_threshold = signal_config.get("short_min_score", 55)

    m15 = tf_metrics.get("15m", {})
    breakout_bull = m15.get("breakout_bull", False)
    breakout_bear = m15.get("breakout_bear", False)

    price = coin_data.get("price", 0)
    klines = coin_data.get("klines", [])
    atr = _get_atr(klines) if klines and price > 0 else price * 0.02
    if atr <= 0:
        atr = price * 0.02
    price_prec = get_price_precision(price)

    signal = "WAIT"
    confidence = 50
    entry = price
    sl = None
    tp = None

    # Confidence calibration: flatten extremes to prevent overconfidence
    # Historical data shows: conf 60s-70s = best WR (62-65%), conf 90s = 48% WR
    # Use sigmoid-like mapping that caps extreme scores
    if score >= long_threshold:
        signal = "LONG"
        sl, tp = _calc_signal_levels(1, entry, atr, price_prec, regime_type, "LONG")
        raw_conf = score
    elif score <= (100 - short_threshold):
        signal = "SHORT"
        sl, tp = _calc_signal_levels(-1, entry, atr, price_prec, regime_type, "SHORT")
        raw_conf = 100 - score
    else:
        raw_conf = max(40, 50 - abs(score - 50))

    # Calibrate: compress extremes with diminishing returns
    # 50→50, 60→60, 70→68, 80→73, 90→76, 95→78
    if raw_conf <= 65:
        confidence = int(raw_conf)
    elif raw_conf <= 75:
        confidence = int(65 + (raw_conf - 65) * 0.6)
    else:
        confidence = int(71 + (raw_conf - 75) * 0.35)

    confidence = max(40, min(85, confidence))

    # Minimum SL distance: at least 0.5% of price (prevent tight SL in low-ATR)
    min_sl_distance = price * 0.005
    if sl and entry:
        current_sl_dist = abs(entry - sl)
        if current_sl_dist < min_sl_distance:
            if signal == "LONG":
                sl = round(entry - min_sl_distance, price_prec)
            elif signal == "SHORT":
                sl = round(entry + min_sl_distance, price_prec)

    reasons = []

    if breakout_bull and signal == "LONG":
        reasons.append("Bullish Breakout")
    elif breakout_bear and signal == "SHORT":
        reasons.append("Bearish Breakout")

    if score >= 75:
        reasons.append("Strong Bullish Momentum")
    elif score >= 60:
        reasons.append("Bullish Momentum")
    elif score <= 25:
        reasons.append("Strong Bearish Momentum")
    elif score <= 40:
        reasons.append("Bearish Momentum")

    if signal == "LONG" and regime_type == "BULL":
        reasons.append("Bullish Trend Regime")
    elif signal == "SHORT" and regime_type == "BEAR":
        reasons.append("Bearish Trend Regime")
    elif signal == "WAIT" and regime_type == "SIDEWAYS":
        reasons.append("Sideways Market")

    # Regime-signal alignment warning (not block!)
    low_combos = {
        ("SIDEWAYS", "LONG"): "Low WR Combo",
        ("BEAR", "SHORT"): "Low WR Combo",
        ("HIGH_VOL", "SHORT"): "Low WR Combo",
    }
    combo_key = (regime_type, signal)
    if combo_key in low_combos and signal != "WAIT":
        reasons.append(f"Caution: {low_combos[combo_key]}")

    vol_z = m15.get("vol_z", 0)
    if vol_z > 2.0:
        reasons.append("High Volume Spike")
    elif vol_z < -1.0:
        reasons.append("Low Volume")

    rsi = m15.get("rsi", 50)
    if rsi > 70:
        reasons.append("Overbought (RSI)")
    elif rsi < 30:
        reasons.append("Oversold (RSI)")

    if len(tf_scores) >= 2:
        scores_list = list(tf_scores.values())
        if all(s >= 60 for s in scores_list):
            reasons.append("All Timeframes Bullish")
        elif all(s <= 40 for s in scores_list):
            reasons.append("All Timeframes Bearish")
        elif max(scores_list) - min(scores_list) > 30:
            reasons.append("Mixed Timeframe Signals")

    # Session info from engine
    session_name = coin_data.get("session", "UNKNOWN")

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
        "patterns_detected": coin_data.get("patterns_detected", []),
        "session": session_name,
        "atr": round(atr, 6),
        "atr_sl_mult": _get_sl_tp_params(regime_type, signal)[0] if signal in ("LONG", "SHORT") else 1.5,
        "atr_tp_mult": _get_sl_tp_params(regime_type, signal)[1] if signal in ("LONG", "SHORT") else 3.0,
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
        tr = pd.Series(np.maximum.reduce([tr1.values, tr2.values, tr3.values]), index=df.index)
        return float(tr.rolling(window=period).mean().iloc[-1])
    except Exception:
        return 0.0