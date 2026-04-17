"""
Market Regime Detection — BULL/BEAR/SIDEWAYS/HIGH_VOL per coin.
Combines trend, volatility, and momentum signals.
"""
import pandas as pd
import numpy as np
import logging
from src.indicators import calculate_adx, calculate_atr, calculate_rsi, calculate_macd

logger = logging.getLogger(__name__)


class RegimeDetector:
    """Detect market regime for a single coin."""

    def __init__(self, adx_threshold: float = 25, vol_lookback: int = 20,
                 atr_period: int = 14):
        self.adx_threshold = adx_threshold
        self.vol_lookback = vol_lookback
        self.atr_period = atr_period

    def detect(self, df: pd.DataFrame) -> dict:
        """
        Detect regime from OHLCV data.

        Returns:
            {regime, confidence, trend_dir, vol_level, momentum}
        """
        trend = self._detect_trend(df)
        vol = self._detect_volatility(df)
        momentum = self._detect_momentum(df)

        regime, confidence = self._combine_signals(trend, vol, momentum)

        return {
            "regime": regime,           # BULL, BEAR, SIDEWAYS, HIGH_VOL
            "confidence": confidence,   # 0-1
            "trend_strength": trend["strength"],
            "trend_direction": trend["direction"],
            "vol_level": vol["level"],  # low, normal, high
            "momentum": momentum["bias"]
        }

    def _detect_trend(self, df: pd.DataFrame) -> dict:
        """Detect trend direction and strength."""
        adx_data = calculate_adx(df, 14)
        adx = adx_data["adx"]
        direction = adx_data["direction"]

        # MA crossover
        close = df["close"]
        ma20 = close.rolling(20).mean()
        ma50 = close.rolling(50).mean()

        ma_diff = ((ma20.iloc[-1] - ma50.iloc[-1]) / ma50.iloc[-1]) * 100 if len(close) >= 50 else 0
        price_vs_ma50 = ((close.iloc[-1] - ma50.iloc[-1]) / ma50.iloc[-1]) * 100 if len(close) >= 50 else 0

        return {
            "adx": adx,
            "direction": direction,
            "ma_diff_pct": round(ma_diff, 2),
            "price_vs_ma50": round(price_vs_ma50, 2),
            "strength": adx_data["trend_strength"]
        }

    def _detect_volatility(self, df: pd.DataFrame) -> dict:
        """Detect volatility regime."""
        atr_data = calculate_atr(df, self.atr_period)
        atr_pct = atr_data["atr_pct"]

        # Rolling volatility
        returns = df["close"].pct_change()
        rolling_vol = returns.rolling(self.vol_lookback).std() * np.sqrt(365) * 100

        current_vol = float(rolling_vol.iloc[-1]) if not rolling_vol.empty else 0
        hist_vol = float(rolling_vol.mean()) if len(rolling_vol.dropna()) > 10 else current_vol

        vol_ratio = current_vol / hist_vol if hist_vol > 0 else 1.0

        if vol_ratio > 1.5:
            level = "high"
        elif vol_ratio < 0.6:
            level = "low"
        else:
            level = "normal"

        return {
            "atr_pct": atr_pct,
            "current_vol": round(current_vol, 2),
            "vol_ratio": round(vol_ratio, 2),
            "level": level
        }

    def _detect_momentum(self, df: pd.DataFrame) -> dict:
        """Detect momentum signals."""
        rsi_data = calculate_rsi(df, 14)
        macd_data = calculate_macd(df)

        # Rate of change
        roc_10 = df["close"].pct_change(10).iloc[-1] * 100
        roc_20 = df["close"].pct_change(20).iloc[-1] * 100 if len(df) >= 20 else roc_10

        # Momentum bias
        if rsi_data["value"] > 60 and roc_10 > 2:
            bias = "strong_bullish"
        elif rsi_data["value"] > 50 and roc_10 > 0:
            bias = "bullish"
        elif rsi_data["value"] < 40 and roc_10 < -2:
            bias = "strong_bearish"
        elif rsi_data["value"] < 50 and roc_10 < 0:
            bias = "bearish"
        else:
            bias = "neutral"

        return {
            "rsi": rsi_data["value"],
            "macd_status": macd_data["status"],
            "roc_10": round(roc_10, 2),
            "roc_20": round(roc_20, 2),
            "bias": bias
        }

    def _combine_signals(self, trend: dict, vol: dict, momentum: dict):
        """Combine signals to determine regime."""
        # High volatility overrides everything
        if vol["level"] == "high" and vol["vol_ratio"] > 2.0:
            return "HIGH_VOL", min(vol["vol_ratio"] / 3, 1.0)

        # Weak trend = sideways
        if trend["adx"] < self.adx_threshold:
            confidence = 1 - (trend["adx"] / self.adx_threshold)
            return "SIDEWAYS", round(confidence * 0.8, 2)

        # Strong trend — determine direction
        score = 0

        # ADX contribution
        score += (trend["adx"] / 50) * 0.3

        # MA contribution
        if trend["ma_diff_pct"] > 0.5:
            score += 0.3
        elif trend["ma_diff_pct"] < -0.5:
            score -= 0.3

        # Momentum contribution
        if "bullish" in momentum["bias"]:
            score += 0.2
        elif "bearish" in momentum["bias"]:
            score -= 0.2

        # Price vs MA
        if trend["price_vs_ma50"] > 2:
            score += 0.2
        elif trend["price_vs_ma50"] < -2:
            score -= 0.2

        if score > 0.3:
            regime = "BULL"
        elif score < -0.3:
            regime = "BEAR"
        else:
            regime = "SIDEWAYS"

        confidence = min(abs(score) + 0.3, 1.0)
        return regime, round(confidence, 2)
