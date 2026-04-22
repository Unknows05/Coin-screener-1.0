"""
Adaptive RL Module — Reinforcement Learning for Signal Optimization.

This module provides:
1. Performance-based factor weight adjustment
2. Adaptive signal thresholds based on win rate
3. Regime-specific parameter optimization
4. Kelly Criterion position sizing recommendations

Uses Q-learning inspired approach with exponential moving averages
for stable weight updates.
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import sqlite3

logger = logging.getLogger(__name__)


class AdaptiveSignalOptimizer:
    """
    Q-Learning inspired adaptive optimizer for trading signals.
    
    Adjusts factor weights and thresholds based on actual trade outcomes.
    Uses exponential moving average for stable updates.
    """
    
    # Default configuration
    DEFAULT_CONFIG = {
        "learning_rate": 0.05,
        "min_samples": 20,
        "ema_span": 50,
        "adjustment_threshold": 0.55,
        "max_weight_change": 0.1,
        "regime_specific": True,
        "decay_factor": 0.95,
        "session_feedback": True,
        "reversal_detection": True,
    }
    
    # Base factor weights (starting point)
    BASE_FACTOR_WEIGHTS = {
        "mean_reversion": 0.25,
        "momentum": 0.30,
        "volume": 0.20,
        "volatility": 0.15,
        "pattern": 0.10,
    }
    
    # Regime-specific adjustments
    REGIME_PROFILES = {
        "BULL": {
            "factor_bias": {"momentum": 1.3, "mean_reversion": 0.7, "volume": 1.1},
            "score_threshold": 50,
            "preferred_signal": "LONG",
        },
        "BEAR": {
            "factor_bias": {"mean_reversion": 1.3, "momentum": 0.8, "volatility": 1.2},
            "score_threshold": 55,
            "preferred_signal": "SHORT",
        },
        "SIDEWAYS": {
            "factor_bias": {"mean_reversion": 1.4, "momentum": 0.6, "pattern": 1.3},
            "score_threshold": 55,
            "preferred_signal": "SHORT",
        },
        "HIGH_VOL": {
            "factor_bias": {"volatility": 1.4, "volume": 1.3, "momentum": 0.8},
            "score_threshold": 55,
            "preferred_signal": None,
        },
    }
    
    def __init__(self, db_path: str = "data/screener.db", config_path: str = "data/adaptive_config.json"):
        self.db_path = db_path
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self.current_weights = self._load_weights()
        self.performance_history: List[Dict] = []
        
    def _load_config(self) -> Dict:
        """Load or create adaptive configuration."""
        if self.config_path.exists():
            with open(self.config_path, 'r') as f:
                return {**self.DEFAULT_CONFIG, **json.load(f)}
        return self.DEFAULT_CONFIG.copy()
    
    def _load_weights(self) -> Dict[str, Dict]:
        """Load current adaptive weights per regime."""
        weights_path = self.config_path.parent / "adaptive_weights.json"
        if weights_path.exists():
            with open(weights_path, 'r') as f:
                return json.load(f)
        
        # Initialize with base weights for each regime
        return {
            regime: self.BASE_FACTOR_WEIGHTS.copy()
            for regime in ["BULL", "BEAR", "SIDEWAYS", "HIGH_VOL", "DEFAULT"]
        }
    
    def _save_weights(self):
        """Persist learned weights."""
        weights_path = self.config_path.parent / "adaptive_weights.json"
        with open(weights_path, 'w') as f:
            json.dump(self.current_weights, f, indent=2)
        logger.info(f"[RL] Saved adaptive weights to {weights_path}")
    
    def calculate_kelly_criterion(self, wins: int, losses: int, avg_win_pct: float, avg_loss_pct: float) -> float:
        """
        Calculate Kelly Criterion for optimal position sizing.
        
        f* = (p × b - q) / b
        where:
        - p = win probability
        - q = loss probability (1-p)
        - b = avg win / avg loss (reward/risk ratio)
        
        Returns: Fraction of capital to risk (0-1), capped at 0.25 for safety
        """
        total = wins + losses
        if total < 10 or avg_loss_pct == 0:
            return 0.02  # Default 2% risk
        
        p = wins / total
        q = 1 - p
        b = avg_win_pct / avg_loss_pct  # Reward-to-risk ratio
        
        if b <= 0:
            return 0.01  # Minimum risk if negative expectancy
        
        kelly = (p * b - q) / b
        
        # Half-Kelly for safety, cap at 25%
        return max(0.01, min(kelly * 0.5, 0.25))
    
    def calculate_expectancy(self, win_rate: float, avg_win_pct: float, avg_loss_pct: float) -> float:
        """
        Calculate trading expectancy.
        
        Expectancy = (Win Rate × Avg Win) - (Loss Rate × Avg Loss)
        
        Positive expectancy = profitable system
        """
        loss_rate = 1 - win_rate
        return (win_rate * avg_win_pct) - (loss_rate * abs(avg_loss_pct))
    
    def analyze_recent_performance(self, days: int = 7, _timeout: float = 10.0) -> Dict:
        """
        Analyze recent trade performance for adaptive adjustments.
        
        Returns performance metrics per regime and factor category.
        """
        conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        try:
            since = (datetime.now() - timedelta(days=days)).isoformat()
            
            c.execute("""
                SELECT 
                    regime,
                    result,
                    signal,
                    entry_price,
                    exit_price,
                    (SELECT AVG(CASE WHEN result='WIN' THEN 1 ELSE 0 END) * 100
                     FROM signals s2 
                     WHERE s2.regime = s1.regime 
                     AND s2.timestamp > ? 
                     AND s2.result IN ('WIN', 'LOSS')) as regime_wr
                FROM signals s1
                WHERE timestamp > ? 
                AND result IN ('WIN', 'LOSS')
                ORDER BY timestamp DESC
            """, (since, since))
            
            rows = c.fetchall()
        except Exception as e:
            logger.error(f"[RL] Performance analysis error: {e}")
            try:
                conn.close()
            except Exception:
                pass
            return {"error": str(e)}
        
        conn.close()
        
        if not rows:
            return {"error": "No completed trades in the specified period"}
        
        # Aggregate by regime
        regime_stats = {}
        for row in rows:
            regime = row["regime"] or "UNKNOWN"
            if regime not in regime_stats:
                regime_stats[regime] = {
                    "wins": 0, "losses": 0, 
                    "win_pnl": [], "loss_pnl": [],
                    "long_wins": 0, "long_losses": 0,
                    "short_wins": 0, "short_losses": 0,
                }
            
            stats = regime_stats[regime]
            entry = row["entry_price"] or 0
            exit_p = row["exit_price"] or 0
            
            if entry > 0 and exit_p > 0:
                pnl_pct = ((exit_p - entry) / entry) * 100
                if row["signal"] == "SHORT":
                    pnl_pct = -pnl_pct  # Invert for shorts
                
                if row["result"] == "WIN":
                    stats["wins"] += 1
                    stats["win_pnl"].append(pnl_pct)
                    if row["signal"] == "LONG":
                        stats["long_wins"] += 1
                    else:
                        stats["short_wins"] += 1
                else:
                    stats["losses"] += 1
                    stats["loss_pnl"].append(abs(pnl_pct))
                    if row["signal"] == "LONG":
                        stats["long_losses"] += 1
                    else:
                        stats["short_losses"] += 1
        
        # Calculate metrics per regime
        results = {}
        for regime, stats in regime_stats.items():
            total = stats["wins"] + stats["losses"]
            if total < self.config["min_samples"]:
                results[regime] = {"status": "INSUFFICIENT_DATA", "samples": total}
                continue
            
            win_rate = stats["wins"] / total
            avg_win = sum(stats["win_pnl"]) / len(stats["win_pnl"]) if stats["win_pnl"] else 0
            avg_loss = sum(stats["loss_pnl"]) / len(stats["loss_pnl"]) if stats["loss_pnl"] else 1
            
            expectancy = self.calculate_expectancy(win_rate, avg_win, avg_loss)
            kelly = self.calculate_kelly_criterion(stats["wins"], stats["losses"], avg_win, avg_loss)
            
            results[regime] = {
                "total_trades": total,
                "wins": stats["wins"],
                "losses": stats["losses"],
                "win_rate": round(win_rate * 100, 2),
                "avg_win_pct": round(avg_win, 2),
                "avg_loss_pct": round(avg_loss, 2),
                "expectancy": round(expectancy, 3),
                "kelly_fraction": round(kelly, 4),
                "long_wr": round(stats["long_wins"] / (stats["long_wins"] + stats["long_losses"]) * 100, 2) if (stats["long_wins"] + stats["long_losses"]) > 0 else 0,
                "short_wr": round(stats["short_wins"] / (stats["short_wins"] + stats["short_losses"]) * 100, 2) if (stats["short_wins"] + stats["short_losses"]) > 0 else 0,
            }
        
        return results
    
    def update_weights(self, performance_data: Optional[Dict] = None) -> Dict:
        """
        Update factor weights based on performance.
        
        Uses performance attribution to adjust weights:
        - If momentum signals perform well → increase momentum weight
        - If mean reversion fails → decrease mean reversion weight
        """
        if performance_data is None:
            performance_data = self.analyze_recent_performance()
        
        lr = self.config["learning_rate"]
        max_change = self.config["max_weight_change"]
        
        updates = {}
        
        for regime, data in performance_data.items():
            if data.get("status") == "INSUFFICIENT_DATA":
                continue
            
            if regime not in self.current_weights:
                self.current_weights[regime] = self.BASE_FACTOR_WEIGHTS.copy()
            
            current = self.current_weights[regime]
            
            # Determine performance trend
            win_rate = data.get("win_rate", 50)
            expectancy = data.get("expectancy", 0)
            
            if win_rate < self.config["adjustment_threshold"] or expectancy < 0:
                # Poor performance → exploration mode (revert toward base slightly)
                for factor in current:
                    base = self.BASE_FACTOR_WEIGHTS[factor]
                    diff = base - current[factor]
                    current[factor] += diff * lr * 0.5  # Slower adjustment back
            else:
                # Good performance → reinforce current weights
                # But apply regime bias
                bias = self.REGIME_PROFILES.get(regime, {}).get("factor_bias", {})
                for factor, weight in current.items():
                    bias_mult = bias.get(factor, 1.0)
                    target = weight * (1 + (win_rate - 50) / 100) * bias_mult
                    diff = target - weight
                    change = max(-max_change, min(max_change, diff * lr))
                    current[factor] = max(0.05, min(0.60, weight + change))
            
            # Normalize to sum to 1
            total = sum(current.values())
            if total > 0:
                current = {k: round(v / total, 3) for k, v in current.items()}
                self.current_weights[regime] = current
            
            updates[regime] = {
                "new_weights": current.copy(),
                "win_rate": win_rate,
                "expectancy": expectancy,
            }
        
        self._save_weights()
        logger.info(f"[RL] Updated weights for {len(updates)} regimes")
        
        return updates
    
    def get_recommended_params(self, regime: str = "DEFAULT") -> Dict:
        """
        Get recommended parameters for a regime.

        Includes session-aware feedback and reversal detection.
        """
        weights = self.current_weights.get(regime, self.current_weights.get("DEFAULT", self.BASE_FACTOR_WEIGHTS))

        perf = self.analyze_recent_performance(days=7)
        regime_perf = perf.get(regime, {})

        preferred_signal = self.REGIME_PROFILES.get(regime, {}).get("preferred_signal")

        return {
            "factor_weights": weights,
            "score_threshold": self.REGIME_PROFILES.get(regime, {}).get("score_threshold", 55),
            "kelly_fraction": regime_perf.get("kelly_fraction", 0.02),
            "expectancy": regime_perf.get("expectancy", 0),
            "recent_wr": regime_perf.get("win_rate", 50),
            "suggested_position_size": f"{regime_perf.get('kelly_fraction', 0.02) * 100:.1f}%",
            "preferred_signal": preferred_signal,
            "long_wr": regime_perf.get("long_wr", 50),
            "short_wr": regime_perf.get("short_wr", 50),
        }

    def detect_regime_reversal(self, recent_days: int = 3) -> Dict:
        """
        Detect if regime performance is reversing.

        Key insight: if SHORT WR was high but now dropping, and LONG WR rising,
        the market may be flipping. System should NOT be blind — it should
        adjust signal direction dynamically.

        Returns dict with reversal signals and direction bias.
        """
        try:
            recent = self.analyze_recent_performance(days=recent_days, _timeout=5.0)
            longer = self.analyze_recent_performance(days=14, _timeout=5.0)
        except Exception:
            return {"reversal_detected": False, "bias_shift": 0}

        reversal_info = {"reversal_detected": False, "bias_shift": 0, "details": {}}

        for regime in ["BULL", "BEAR", "SIDEWAYS", "HIGH_VOL"]:
            r_data = recent.get(regime, {})
            l_data = longer.get(regime, {})

            if r_data.get("status") == "INSUFFICIENT_DATA":
                continue
            if l_data.get("status") == "INSUFFICIENT_DATA":
                continue

            r_long_wr = r_data.get("long_wr", 50)
            r_short_wr = r_data.get("short_wr", 50)
            l_long_wr = l_data.get("long_wr", 50)
            l_short_wr = l_data.get("short_wr", 50)

            long_delta = r_long_wr - l_long_wr
            short_delta = r_short_wr - l_short_wr

            if abs(long_delta) > 15 or abs(short_delta) > 15:
                reversal_info["reversal_detected"] = True
                if long_delta > 0 and short_delta < 0:
                    shift = min(10, abs(long_delta) * 0.3)
                    reversal_info["bias_shift"] = shift
                    reversal_info["details"][regime] = {
                        "direction": "LONG_BIAS_INCREASING",
                        "long_delta": round(long_delta, 1),
                        "short_delta": round(short_delta, 1),
                    }
                elif short_delta > 0 and long_delta < 0:
                    shift = max(-10, -abs(short_delta) * 0.3)
                    reversal_info["bias_shift"] = shift
                    reversal_info["details"][regime] = {
                        "direction": "SHORT_BIAS_INCREASING",
                        "long_delta": round(long_delta, 1),
                        "short_delta": round(short_delta, 1),
                    }

        return reversal_info
    
    def generate_report(self) -> str:
        """Generate a human-readable RL performance report."""
        perf = self.analyze_recent_performance(days=14)
        
        lines = [
            "=" * 60,
            "📊 ADAPTIVE LEARNING REPORT",
            "=" * 60,
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "Current Factor Weights by Regime:",
            "-" * 60,
        ]
        
        for regime, weights in self.current_weights.items():
            lines.append(f"\n[{regime}]")
            for factor, weight in weights.items():
                bar = "█" * int(weight * 20)
                lines.append(f"  {factor:15s}: {weight:.3f} {bar}")
            
            # Add performance if available
            if regime in perf and "win_rate" in perf[regime]:
                p = perf[regime]
                lines.append(f"  Performance: {p['win_rate']:.1f}% WR | Expectancy: {p['expectancy']:.3f}")
                lines.append(f"  Kelly Size: {p['kelly_fraction']*100:.2f}% | Trades: {p['total_trades']}")
        
        lines.extend([
            "",
            "=" * 60,
            "💡 RECOMMENDATIONS",
            "=" * 60,
        ])
        
        # Generate recommendations
        best_regime = None
        best_expectancy = -999
        for regime, data in perf.items():
            if "expectancy" in data and data["expectancy"] > best_expectancy:
                best_expectancy = data["expectancy"]
                best_regime = regime
        
        if best_regime and best_expectancy > 0:
            lines.append(f"✓ Best performing regime: {best_regime} (Expectancy: {best_expectancy:.3f})")
            lines.append(f"✓ Focus trading on {best_regime} conditions for optimal edge")
        
        # Warnings
        for regime, data in perf.items():
            if data.get("expectancy", 0) < 0:
                lines.append(f"⚠ Avoid {regime} regime - Negative expectancy detected")
        
        lines.append("")
        return "\n".join(lines)


# Singleton instance
_adaptive_optimizer: Optional[AdaptiveSignalOptimizer] = None


def get_optimizer(db_path: str = "data/screener.db") -> AdaptiveSignalOptimizer:
    """Get or create the adaptive optimizer singleton."""
    global _adaptive_optimizer
    if _adaptive_optimizer is None:
        _adaptive_optimizer = AdaptiveSignalOptimizer(db_path)
    return _adaptive_optimizer


def quick_analysis() -> str:
    """Quick performance analysis for CLI/logging."""
    opt = get_optimizer()
    return opt.generate_report()


if __name__ == "__main__":
    # CLI test
    print(quick_analysis())
