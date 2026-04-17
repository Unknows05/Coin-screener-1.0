"""
Scorer Engine — Advanced Quantitative Analysis.
Refactored for Balanced Momentum & Breakout Detection.
"""
import pandas as pd
import logging

logger = logging.getLogger(__name__)

class Scorer:
    """
    Multi-Timeframe Scorer with Breakout Detection.
    Uses BB Squeeze, Volume Z-Score, and RSI Momentum.
    """

    def __init__(self, config: dict):
        self.config = config
        # Logic Gate: 15m dominates for timing
        self.tf_weights = config.get("timeframe_weights", {"15m": 0.60, "1h": 0.30, "4h": 0.10})
        self.squeeze_threshold = config.get("scan", {}).get("squeeze_threshold", 0.06)

    def score_coin(self, klines_by_tf: dict[str, list[dict]]) -> dict:
        """
        Score a coin based on multi-TF logic and anomaly detection.
        Returns score 0-100 where:
        - 0-40: Bearish zone (SHORT candidates)
        - 40-60: Neutral zone (WAIT)
        - 60-100: Bullish zone (LONG candidates)
        """
        tf_scores = {}
        tf_metrics = {}  # Store detailed metrics per timeframe
        
        # 1. Analyze Each Timeframe
        for tf, klines in klines_by_tf.items():
            if not klines or len(klines) < 20:
                continue  # Lowered from 50 to 20 for faster detection
            
            try:
                df = pd.DataFrame(klines)

                # --- Metrics ---
                rsi = self._calc_rsi(df, 14)
                macd_hist = self._calc_macd_histogram(df)
                vol_z = self._calc_volume_zscore(df)

                current_price = df["close"].iloc[-1]
                
                # Compute Bollinger Bands once (reused for width and upper)
                bb_sma = df["close"].rolling(window=20).mean()
                bb_std = df["close"].rolling(window=20).std()
                bb_upper = bb_sma + (2 * bb_std)
                bb_lower = bb_sma - (2 * bb_std)
                bb_width = (bb_upper - bb_lower) / bb_sma
                
                # --- Standard Scoring ---
                # RSI Momentum (centered at 50)
                rsi_val = rsi.iloc[-1]
                rsi_score = (rsi_val - 50) * 0.8  # Range -40 to 40
                
                # MACD Trend (Normalized by Price to avoid scaling issues)
                macd_val = macd_hist.iloc[-1]
                macd_score = (macd_val / current_price) * 8000  # Scale to percentage points
                
                # Volume Anomaly
                vol_z_val = vol_z.iloc[-1]
                vol_score = 0
                if vol_z_val > 2.0:
                    vol_score = 8
                elif vol_z_val < -1.0:
                    vol_score = -3

                # Price position within Bollinger Bands
                price_vs_upper = (current_price - bb_upper.iloc[-1]) / bb_std.iloc[-1] if bb_std.iloc[-1] > 0 else 0
                price_vs_lower = (current_price - bb_lower.iloc[-1]) / bb_std.iloc[-1] if bb_std.iloc[-1] > 0 else 0
                
                # Breakout detection (more strict)
                bb_width_current = bb_width.iloc[-1]
                is_squeeze = bb_width_current < self.squeeze_threshold
                
                breakout_bull = False
                breakout_bear = False
                
                if is_squeeze:
                    # After squeeze, direction matters
                    if current_price > bb_upper.iloc[-1]:
                        breakout_bull = True
                    elif current_price < bb_lower.iloc[-1]:
                        breakout_bear = True
                
                # Calculate raw score (centered at 50)
                raw_score = 50 + rsi_score + macd_score + vol_score
                
                # Apply breakout bonus/penalty
                if breakout_bull:
                    raw_score += 15
                elif breakout_bear:
                    raw_score -= 15
                
                # Clamp to 0-100 range
                final_tf_score = max(0, min(100, raw_score))
                
                tf_scores[tf] = final_tf_score
                tf_metrics[tf] = {
                    "rsi": round(rsi_val, 1),
                    "macd": round(macd_val, 4),
                    "vol_z": round(vol_z_val, 2),
                    "bb_width": round(bb_width_current, 4),
                    "breakout_bull": breakout_bull,
                    "breakout_bear": breakout_bear,
                    "price_vs_bb_upper": round(price_vs_upper, 2),
                    "price_vs_bb_lower": round(price_vs_lower, 2),
                }
                
            except Exception as e:
                logger.warning(f"Scoring error on {tf}: {e}")
                continue

        # 2. Composite Score Logic
        final_score = 50  # Default neutral
        
        if tf_scores:
            score_15m = tf_scores.get("15m", 50)
            score_1h = tf_scores.get("1h", score_15m)  # Fallback to 15m if missing
            score_4h = tf_scores.get("4h", score_1h)   # Fallback to 1h if missing
            
            # Logic: Higher timeframe trend should align with lower timeframe
            # If 4h bullish but 15m bearish = caution
            # If all align = stronger signal
            
            trend_alignment = 0
            if score_4h > 60 and score_15m > 60:
                trend_alignment = 10  # Strong uptrend alignment
            elif score_4h < 40 and score_15m < 40:
                trend_alignment = -10  # Strong downtrend alignment
            elif abs(score_4h - score_15m) > 30:
                trend_alignment = -5  # Conflicting signals
            
            # Weighted average with all timeframes
            total_weight = 0
            weighted_sum = 0
            for tf, score in tf_scores.items():
                weight = self.tf_weights.get(tf, 0.1)
                total_weight += weight
                weighted_sum += score * weight
            
            if total_weight > 0:
                final_score = (weighted_sum / total_weight) + trend_alignment
                # Clamp final score
                final_score = max(0, min(100, final_score))
            else:
                final_score = 50

        return {
            "composite_score": round(final_score, 1),
            "tf_scores": tf_scores,
            "tf_metrics": tf_metrics,
        }

    # --- Math Helpers (Robust) ---

    def _calc_rsi(self, df: pd.DataFrame, period: int) -> pd.Series:
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / (loss + 1e-10)
        return 100 - (100 / (1 + rs))

    def _calc_macd_histogram(self, df: pd.DataFrame) -> pd.Series:
        ema12 = df["close"].ewm(span=12, adjust=False).mean()
        ema26 = df["close"].ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        return macd - signal

    def _calc_volume_zscore(self, df: pd.DataFrame) -> pd.Series:
        vol_mean = df["volume"].rolling(window=20).mean()
        vol_std = df["volume"].rolling(window=20).std()
        return (df["volume"] - vol_mean) / (vol_std + 1e-10)
