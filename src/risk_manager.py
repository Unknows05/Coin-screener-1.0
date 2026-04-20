"""
Risk Management & Protection Module

Mengatasi 4 risiko utama:
1. Overfitting - Walk-forward validation, conservative adaptation
2. Regime Change - Auto-pause during uncertainty, regime-specific models
3. Liquidity Issues - Real-time slippage estimation, volume filtering
4. Black Swan Events - Circuit breakers, emergency shutdown

Integrasi dengan bot yang sudah ada.
"""
import json
import logging
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    """Konfigurasi risk management."""
    # Daily limits
    max_daily_loss_pct: float = 0.02  # 2% per day
    max_daily_trades: int = 10  # Max 10 trades per day
    
    # Consecutive limits
    max_consecutive_losses: int = 3
    max_consecutive_wins: int = 10  # Take profit day after 10 wins
    
    # Drawdown limits
    max_drawdown_pct: float = 0.10  # 10% max drawdown
    warning_drawdown_pct: float = 0.05  # 5% warning
    
    # Volatility limits
    max_volatility_spike: float = 2.0  # 2x normal volatility
    
    # Liquidity limits
    min_order_book_depth_usd: float = 100000  # $100k depth 1%
    max_slippage_pct: float = 0.5  # 0.5% max slippage
    
    # Position sizing
    max_position_size_pct: float = 0.25  # 25% max (Half-Kelly)
    min_position_size_pct: float = 0.01  # 1% min
    
    # Correlation limits
    max_correlation_exposure: float = 0.60  # 60% max correlation
    
    # Circuit breaker
    circuit_breaker_enabled: bool = True
    auto_recovery_minutes: int = 60  # Auto-check after 1 hour


class OverfittingProtector:
    """
    Proteksi terhadap overfitting.
    
    Mekanisme:
    1. Walk-forward validation
    2. Out-of-sample testing
    3. Conservative weight updates
    4. Regime stability checks
    """
    
    def __init__(self, db_path: str = "data/screener.db"):
        self.db_path = db_path
        self.validation_window = 100  # Last 100 trades for validation
        self.overfitting_threshold = 10  # 10% performance drop = overfitting
        
    def validate_out_of_sample(self, regime: str = None) -> Dict:
        """
        Validasi performance pada data yang belum dilihat (recent trades).
        
        Returns:
            {
                "is_overfitting": bool,
                "train_win_rate": float,
                "test_win_rate": float,
                "performance_drop": float,
                "recommendation": str
            }
        """
        try:
            from src.database import ScreenerDB
            db = ScreenerDB(self.db_path)
            
            # Get all signals
            signals = db.get_signals_with_outcomes(limit=500)
            
            if len(signals) < 200:
                return {
                    "is_overfitting": False,
                    "train_win_rate": 0,
                    "test_win_rate": 0,
                    "performance_drop": 0,
                    "recommendation": "INSUFFICIENT_DATA",
                    "message": f"Need at least 200 signals, have {len(signals)}"
                }
            
            # Split: Training = older 80%, Test = recent 20%
            split_idx = int(len(signals) * 0.8)
            train_signals = signals[:split_idx]
            test_signals = signals[split_idx:]
            
            # Filter by regime if specified
            if regime:
                train_signals = [s for s in train_signals if s.get("regime") == regime]
                test_signals = [s for s in test_signals if s.get("regime") == regime]
            
            # Calculate win rates
            train_wins = sum(1 for s in train_signals if s.get("result") == "WIN")
            train_total = len([s for s in train_signals if s.get("result") in ("WIN", "LOSS")])
            train_wr = (train_wins / train_total * 100) if train_total > 0 else 0
            
            test_wins = sum(1 for s in test_signals if s.get("result") == "WIN")
            test_total = len([s for s in test_signals if s.get("result") in ("WIN", "LOSS")])
            test_wr = (test_wins / test_total * 100) if test_total > 0 else 0
            
            # Calculate performance drop
            performance_drop = train_wr - test_wr
            is_overfitting = performance_drop > self.overfitting_threshold
            
            recommendation = "OK"
            if is_overfitting:
                recommendation = "OVERFITTING_DETECTED"
            elif performance_drop > 5:
                recommendation = "WARNING_PERFORMANCE_DECLINE"
            elif test_wr < 50:
                recommendation = "POOR_RECENT_PERFORMANCE"
            
            return {
                "is_overfitting": is_overfitting,
                "train_win_rate": round(train_wr, 2),
                "test_win_rate": round(test_wr, 2),
                "performance_drop": round(performance_drop, 2),
                "recommendation": recommendation,
                "train_samples": train_total,
                "test_samples": test_total,
                "message": f"Train WR: {train_wr:.1f}%, Test WR: {test_wr:.1f}%, Drop: {performance_drop:.1f}%"
            }
            
        except Exception as e:
            logger.error(f"[OverfittingProtector] Validation error: {e}")
            return {
                "is_overfitting": False,
                "recommendation": "ERROR",
                "message": str(e)
            }
    
    def check_regime_stability(self, current_regime: str, min_stable_hours: int = 24) -> Dict:
        """
        Check if regime has been stable long enough.
        
        New regimes (< 24 hours) = high uncertainty = reduce trading.
        """
        # This would need regime history tracking in database
        # For now, return conservative recommendation
        
        return {
            "regime": current_regime,
            "stable_enough": True,  # Would check actual duration
            "recommendation": "REDUCE_SIZE_50" if current_regime == "HIGH_VOL" else "NORMAL",
            "message": f"Regime {current_regime}: Trading with caution"
        }


class RegimeChangeProtector:
    """
    Proteksi terhadap perubahan regime market.
    
    Mekanisme:
    1. Regime duration tracking
    2. Uncertainty reduction during transitions
    3. Auto-pause during chaos
    """
    
    def __init__(self):
        self.regime_history = []
        self.min_regime_duration_hours = 6  # Minimum 6 hours stable
        self.uncertainty_threshold = 0.3   # ADX < 30 = uncertainty
        
    def detect_regime_transition(self, current_regime: str, regime_strength: float) -> Dict:
        """
        Deteksi transisi regime dan berikan rekomendasi.
        
        Returns:
            {
                "action": "NORMAL" | "REDUCE" | "PAUSE",
                "confidence": float,
                "reason": str
            }
        """
        # Add to history
        self.regime_history.append({
            "regime": current_regime,
            "strength": regime_strength,
            "timestamp": datetime.now()
        })
        
        # Keep only last 100 entries
        if len(self.regime_history) > 100:
            self.regime_history = self.regime_history[-100:]
        
        # Check for frequent regime changes (choppy market)
        recent_regimes = [h["regime"] for h in self.regime_history[-10:]]
        unique_regimes = len(set(recent_regimes))
        
        if unique_regimes >= 4:  # 4+ different regimes in last 10 periods
            return {
                "action": "PAUSE",
                "confidence": 0.3,
                "reason": "CHOPPY_MARKET: 4+ regime changes detected"
            }
        
        # Check regime strength (ADX proxy)
        if regime_strength < self.uncertainty_threshold:
            return {
                "action": "REDUCE",
                "confidence": regime_strength,
                "reason": f"LOW_REGIME_CONFIDENCE: {regime_strength:.2f} < {self.uncertainty_threshold}"
            }
        
        return {
            "action": "NORMAL",
            "confidence": regime_strength,
            "reason": "Regime stable"
        }
    
    def get_regime_recommendation(self, regime: str) -> Dict:
        """
        Get trading recommendation for specific regime.
        """
        recommendations = {
            "BULL": {
                "action": "CAUTION",
                "bias": "LONG_ONLY",
                "max_position": 0.15,  # 15% max in BULL
                "reason": "BULL markets have lower win rate in our data"
            },
            "BEAR": {
                "action": "FOCUS",
                "bias": "SHORT_FRIENDLY",
                "max_position": 0.20,
                "reason": "BEAR markets favor short signals"
            },
            "SIDEWAYS": {
                "action": "OPTIMAL",
                "bias": "MEAN_REVERSION",
                "max_position": 0.25,
                "reason": "SIDEWAYS is our best performing regime"
            },
            "HIGH_VOL": {
                "action": "OPPORTUNITY",
                "bias": "BREAKOUT",
                "max_position": 0.20,
                "reason": "HIGH_VOL has high expectancy but high risk"
            }
        }
        
        return recommendations.get(regime, {
            "action": "NEUTRAL",
            "bias": "BALANCED",
            "max_position": 0.15,
            "reason": "Unknown regime"
        })


class LiquidityProtector:
    """
    Proteksi terhadap liquidity issues dan slippage.
    
    Mekanisme:
    1. Real-time order book analysis
    2. Slippage estimation
    3. Dynamic position sizing based on liquidity
    """
    
    def __init__(self, config: RiskConfig = None):
        self.config = config or RiskConfig()
        
    def analyze_liquidity(self, symbol: str, order_book: Dict, intended_position_usd: float) -> Dict:
        """
        Analisis liquidity dan estimasi slippage.
        
        Args:
            symbol: Trading pair
            order_book: {bids: [[price, qty], ...], asks: [...]}
            intended_position_usd: Target position size in USD
            
        Returns:
            {
                "liquidity_score": float,  # 0-100
                "estimated_slippage_pct": float,
                "recommended_max_position": float,
                "can_execute": bool,
                "reason": str
            }
        """
        try:
            bids = order_book.get("bids", [])
            asks = order_book.get("asks", [])
            
            if not bids or not asks:
                return {
                    "liquidity_score": 0,
                    "estimated_slippage_pct": 100,
                    "recommended_max_position": 0,
                    "can_execute": False,
                    "reason": "No order book data"
                }
            
            # Calculate mid price
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            mid_price = (best_bid + best_ask) / 2
            
            # Calculate depth within 1% of mid
            depth_1pct_threshold = mid_price * 0.01
            
            bid_depth = sum(
                float(qty) * float(price)
                for price, qty in bids
                if float(price) >= mid_price - depth_1pct_threshold
            )
            
            ask_depth = sum(
                float(qty) * float(price)
                for price, qty in asks
                if float(price) <= mid_price + depth_1pct_threshold
            )
            
            min_depth = min(bid_depth, ask_depth)
            
            # Liquidity score (0-100)
            liquidity_score = min(100, (min_depth / self.config.min_order_book_depth_usd) * 100)
            
            # Estimate slippage using square root formula
            # Slippage ≈ k * sqrt(order_size / depth)
            k = 0.5  # Market impact coefficient
            if min_depth > 0:
                estimated_slippage = k * np.sqrt(intended_position_usd / min_depth) * 100
            else:
                estimated_slippage = 100  # Infinite slippage
            
            # Can we execute?
            can_execute = (
                min_depth >= self.config.min_order_book_depth_usd and
                estimated_slippage <= self.config.max_slippage_pct
            )
            
            # Recommended position (max 50% of depth to minimize impact)
            recommended_position = min(
                intended_position_usd,
                min_depth * 0.5,  # Don't consume more than 50% of depth
                self.calculate_max_position_from_slippage(mid_price, min_depth)
            )
            
            return {
                "liquidity_score": round(liquidity_score, 2),
                "estimated_slippage_pct": round(estimated_slippage, 3),
                "bid_depth_1pct": round(bid_depth, 2),
                "ask_depth_1pct": round(ask_depth, 2),
                "recommended_max_position": round(recommended_position, 2),
                "can_execute": can_execute,
                "reason": "OK" if can_execute else f"Insufficient liquidity: {min_depth:.0f} < {self.config.min_order_book_depth_usd}"
            }
            
        except Exception as e:
            logger.error(f"[LiquidityProtector] Analysis error: {e}")
            return {
                "liquidity_score": 0,
                "estimated_slippage_pct": 100,
                "can_execute": False,
                "reason": f"Error: {str(e)}"
            }
    
    def calculate_max_position_from_slippage(self, price: float, depth: float, max_slippage: float = None) -> float:
        """
        Calculate max position size given slippage constraint.
        
        Formula: max_position = depth * (max_slippage / k)^2
        """
        if max_slippage is None:
            max_slippage = self.config.max_slippage_pct
        
        k = 0.5  # Impact coefficient
        max_slippage_decimal = max_slippage / 100
        
        if max_slippage_decimal <= 0:
            return 0
        
        max_position = depth * (max_slippage_decimal / k) ** 2
        return max_position


class BlackSwanProtector:
    """
    Proteksi terhadap black swan events dan emergency situations.
    
    Mekanisme:
    1. Circuit breakers (daily loss, consecutive losses, drawdown)
    2. Flash crash detection
    3. Auto-shutdown dan manual reset requirement
    """
    
    def __init__(self, config: RiskConfig = None):
        self.config = config or RiskConfig()
        self.circuit_breaker_triggered = False
        self.trigger_reason = None
        self.trigger_timestamp = None
        self.daily_stats = {
            "date": datetime.now().date(),
            "starting_equity": 10000,  # Example
            "current_equity": 10000,
            "trades": [],
            "consecutive_losses": 0,
            "consecutive_wins": 0
        }
        
    def check_all_protections(self, portfolio: Dict, market_data: Dict) -> Dict:
        """
        Run all protection checks.
        
        Returns:
            {
                "trading_allowed": bool,
                "reasons_blocked": List[str],
                "warnings": List[str],
                "actions_taken": List[str]
            }
        """
        results = {
            "trading_allowed": True,
            "reasons_blocked": [],
            "warnings": [],
            "actions_taken": []
        }
        
        # 1. Check circuit breaker
        if self.circuit_breaker_triggered:
            results["trading_allowed"] = False
            results["reasons_blocked"].append(
                f"CIRCUIT_BREAKER_ACTIVE: {self.trigger_reason} at {self.trigger_timestamp}"
            )
            return results
        
        # 2. Daily loss limit
        daily_loss = self.calculate_daily_loss()
        if daily_loss > self.config.max_daily_loss_pct:
            self.trigger_circuit_breaker("DAILY_LOSS_LIMIT", daily_loss)
            results["trading_allowed"] = False
            results["reasons_blocked"].append(f"DAILY_LOSS_LIMIT: {daily_loss:.2%}")
            results["actions_taken"].append("CIRCUIT_BREAKER_TRIGGERED")
            return results
        
        elif daily_loss > self.config.max_daily_loss_pct * 0.5:
            results["warnings"].append(f"DAILY_LOSS_WARNING: {daily_loss:.2%}")
        
        # 3. Consecutive losses
        if self.daily_stats["consecutive_losses"] >= self.config.max_consecutive_losses:
            self.trigger_circuit_breaker("CONSECUTIVE_LOSSES", self.daily_stats["consecutive_losses"])
            results["trading_allowed"] = False
            results["reasons_blocked"].append(f"CONSECUTIVE_LOSSES: {self.daily_stats['consecutive_losses']}")
            results["actions_taken"].append("CIRCUIT_BREAKER_TRIGGERED")
            return results
        
        # 4. Drawdown
        drawdown = self.calculate_drawdown()
        if drawdown > self.config.max_drawdown_pct:
            self.trigger_circuit_breaker("MAX_DRAWDOWN", drawdown)
            results["trading_allowed"] = False
            results["reasons_blocked"].append(f"MAX_DRAWDOWN: {drawdown:.2%}")
            results["actions_taken"].append("CIRCUIT_BREAKER_TRIGGERED")
            return results
        elif drawdown > self.config.warning_drawdown_pct:
            results["warnings"].append(f"DRAWDOWN_WARNING: {drawdown:.2%}")
        
        # 5. Flash crash detection
        flash_crash = self.detect_flash_crash(market_data)
        if flash_crash["detected"]:
            self.trigger_circuit_breaker("FLASH_CRASH", flash_crash["details"])
            results["trading_allowed"] = False
            results["reasons_blocked"].append(f"FLASH_CRASH: {flash_crash['details']}")
            results["actions_taken"].append("EMERGENCY_SHUTDOWN")
            return results
        
        # 6. Volatility spike
        vol_spike = self.detect_volatility_spike(market_data)
        if vol_spike["detected"]:
            results["warnings"].append(f"VOLATILITY_SPIKE: {vol_spike['ratio']:.2f}x normal")
            if vol_spike["ratio"] > self.config.max_volatility_spike * 1.5:
                results["trading_allowed"] = False
                results["reasons_blocked"].append("EXTREME_VOLATILITY")
        
        return results
    
    def trigger_circuit_breaker(self, reason: str, value: any):
        """Trigger circuit breaker dengan manual reset requirement."""
        self.circuit_breaker_triggered = True
        self.trigger_reason = f"{reason}: {value}"
        self.trigger_timestamp = datetime.now()
        
        logger.critical(f"🚨 CIRCUIT BREAKER TRIGGERED: {self.trigger_reason}")
        
        # Log to file
        self.log_emergency_event(reason, value)
        
        # Send alert (placeholder)
        self.send_emergency_alert(self.trigger_reason)
    
    def reset_circuit_breaker(self, manual: bool = True) -> bool:
        """
        Reset circuit breaker.
        
        Args:
            manual: True jika manual reset oleh user, False jika auto-reset
            
        Returns:
            bool: True if successfully reset
        """
        if not self.circuit_breaker_triggered:
            return True
        
        if manual:
            # Require manual confirmation
            logger.info("🔓 Manual circuit breaker reset requested")
            # In real implementation, require authentication
            self.circuit_breaker_triggered = False
            self.trigger_reason = None
            self.daily_stats["consecutive_losses"] = 0  # Reset counter
            logger.info("✅ Circuit breaker reset manually")
            return True
        else:
            # Auto-reset after cooldown period
            if self.trigger_timestamp:
                elapsed = (datetime.now() - self.trigger_timestamp).total_seconds()
                cooldown_seconds = self.config.auto_recovery_minutes * 60
                
                if elapsed > cooldown_seconds:
                    self.circuit_breaker_triggered = False
                    logger.info(f"✅ Auto-reset after {self.config.auto_recovery_minutes} minutes")
                    return True
                else:
                    remaining = cooldown_seconds - elapsed
                    logger.warning(f"⏳ Auto-reset in {remaining/60:.1f} minutes")
                    return False
        
        return False
    
    def calculate_daily_loss(self) -> float:
        """Calculate today's loss percentage."""
        if self.daily_stats["starting_equity"] == 0:
            return 0
        
        pnl = self.daily_stats["current_equity"] - self.daily_stats["starting_equity"]
        return abs(pnl / self.daily_stats["starting_equity"])
    
    def calculate_drawdown(self) -> float:
        """Calculate current drawdown from peak."""
        # Would need historical equity curve
        # Placeholder implementation
        peak = max(self.daily_stats["starting_equity"], self.daily_stats["current_equity"])
        trough = min(self.daily_stats["starting_equity"], self.daily_stats["current_equity"])
        
        if peak == 0:
            return 0
        
        return (peak - trough) / peak
    
    def detect_flash_crash(self, market_data: Dict) -> Dict:
        """
        Detect flash crash (>10% drop in 1 minute).
        
        Returns:
            {"detected": bool, "details": str}
        """
        for symbol, data in market_data.items():
            price_change_1m = data.get("price_change_1m", 0)
            
            if price_change_1m < -0.10:  # -10% in 1 minute
                return {
                    "detected": True,
                    "details": f"{symbol} dropped {price_change_1m:.1%} in 1 minute"
                }
        
        return {"detected": False, "details": ""}
    
    def detect_volatility_spike(self, market_data: Dict) -> Dict:
        """Detect unusual volatility."""
        # Placeholder - would compare to historical volatility
        current_vol = market_data.get("average_volatility", 0.5)
        normal_vol = 0.3  # Historical average
        
        ratio = current_vol / normal_vol if normal_vol > 0 else 0
        
        return {
            "detected": ratio > self.config.max_volatility_spike,
            "ratio": ratio
        }
    
    def log_emergency_event(self, reason: str, value: any):
        """Log emergency event to file."""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "reason": reason,
            "value": value,
            "equity": self.daily_stats["current_equity"],
            "drawdown": self.calculate_drawdown()
        }
        
        log_file = Path("data/emergency_events.jsonl")
        with open(log_file, "a") as f:
            f.write(json.dumps(log_entry) + "\n")
    
    def send_emergency_alert(self, message: str):
        """Send emergency alert (placeholder)."""
        # Would integrate with Telegram, Discord, Email
        logger.critical(f"🚨 EMERGENCY ALERT: {message}")


class RiskManager:
    """
    Main Risk Manager that orchestrates all protectors.
    """
    
    def __init__(self, config: RiskConfig = None, db_path: str = "data/screener.db"):
        self.config = config or RiskConfig()
        self.db_path = db_path
        
        # Initialize all protectors
        self.overfitting_protector = OverfittingProtector(db_path)
        self.regime_protector = RegimeChangeProtector()
        self.liquidity_protector = LiquidityProtector(config)
        self.black_swan_protector = BlackSwanProtector(config)
        
        logger.info("[RiskManager] Initialized all protectors")
    
    def can_trade(self, signal: Dict, market_data: Dict = None) -> Dict:
        """
        Master function: Check if trading is allowed for this signal.
        
        Args:
            signal: Signal dict dengan symbol, price, etc.
            market_data: Optional market context
            
        Returns:
            {
                "allowed": bool,
                "reason": str,
                "risk_score": float,  # 0-100, higher = safer
                "recommended_position": float,
                "warnings": List[str]
            }
        """
        # 1. Check black swan / circuit breakers
        protection_results = self.black_swan_protector.check_all_protections(
            portfolio={},
            market_data=market_data or {}
        )
        
        if not protection_results["trading_allowed"]:
            return {
                "allowed": False,
                "reason": "; ".join(protection_results["reasons_blocked"]),
                "risk_score": 0,
                "recommended_position": 0,
                "warnings": protection_results["warnings"]
            }
        
        # 2. Check overfitting
        regime = signal.get("regime", "SIDEWAYS")
        overfit_check = self.overfitting_protector.validate_out_of_sample(regime)
        
        if overfit_check.get("is_overfitting"):
            return {
                "allowed": False,
                "reason": f"OVERFITTING_DETECTED: {overfit_check['message']}",
                "risk_score": 10,
                "recommended_position": 0,
                "warnings": ["Model may be overfitted, manual review required"]
            }
        
        # 3. Check regime stability
        regime_rec = self.regime_protector.get_regime_recommendation(regime)
        
        if regime_rec["action"] == "PAUSE":
            return {
                "allowed": False,
                "reason": f"REGIME_NOT_FAVORABLE: {regime_rec['reason']}",
                "risk_score": 20,
                "recommended_position": 0,
                "warnings": [f"Current regime {regime} not optimal"]
            }
        
        # 4. Calculate risk score (0-100)
        risk_score = self.calculate_risk_score(signal, overfit_check, regime_rec)
        
        # 5. Determine position size
        base_position = regime_rec.get("max_position", 0.15)
        adjusted_position = base_position * (risk_score / 100)
        
        return {
            "allowed": True,
            "reason": "OK",
            "risk_score": round(risk_score, 1),
            "recommended_position": round(adjusted_position, 3),
            "warnings": protection_results["warnings"] + [overfit_check.get("recommendation", "")]
        }
    
    def calculate_risk_score(self, signal: Dict, overfit_check: Dict, regime_rec: Dict) -> float:
        """
        Calculate composite risk score (0-100).
        Higher = safer to trade.
        """
        score = 50  # Base score
        
        # Confidence boost
        confidence = signal.get("confidence", 50)
        score += (confidence - 50) * 0.3  # ±15 points
        
        # Regime boost
        if regime_rec["action"] == "OPTIMAL":
            score += 20
        elif regime_rec["action"] == "FOCUS":
            score += 10
        elif regime_rec["action"] == "CAUTION":
            score -= 15
        
        # Overfitting penalty
        if overfit_check.get("performance_drop", 0) > 5:
            score -= 20
        
        # Clamp to 0-100
        return max(0, min(100, score))
    
    def get_status_report(self) -> Dict:
        """Get comprehensive risk status report."""
        return {
            "circuit_breaker": {
                "active": self.black_swan_protector.circuit_breaker_triggered,
                "reason": self.black_swan_protector.trigger_reason,
                "timestamp": self.black_swan_protector.trigger_timestamp.isoformat() if self.black_swan_protector.trigger_timestamp else None
            },
            "overfitting": self.overfitting_protector.validate_out_of_sample(),
            "daily_stats": self.black_swan_protector.daily_stats,
            "config": {
                "max_daily_loss": self.config.max_daily_loss_pct,
                "max_drawdown": self.config.max_drawdown_pct,
                "max_consecutive_losses": self.config.max_consecutive_losses
            }
        }


# Singleton instance
_risk_manager: Optional[RiskManager] = None


def get_risk_manager(config: RiskConfig = None, db_path: str = "data/screener.db") -> RiskManager:
    """Get or create RiskManager singleton."""
    global _risk_manager
    if _risk_manager is None:
        _risk_manager = RiskManager(config, db_path)
    return _risk_manager


if __name__ == "__main__":
    # Test
    logging.basicConfig(level=logging.INFO)
    
    rm = get_risk_manager()
    
    # Test signal
    test_signal = {
        "symbol": "BTCUSDT",
        "signal": "LONG",
        "confidence": 78,
        "regime": "SIDEWAYS",
        "price": 71073.1
    }
    
    result = rm.can_trade(test_signal)
    print(json.dumps(result, indent=2))
    
    # Status report
    report = rm.get_status_report()
    print("\nStatus Report:")
    print(json.dumps(report, indent=2, default=str))
