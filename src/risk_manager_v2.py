"""
Risk Manager V2 Extension — Microstructure-Based Risk Protection.

Extends RiskManager dengan enhanced data untuk:
1. Liquidation cascade detection (circuit breaker)
2. Whale divergence warnings
3. Extreme sentiment filtering
4. Order book wall proximity checks

Integrasi seamless dengan RiskManager existing.
"""
import logging
from typing import Dict, List, Optional
from datetime import datetime

from src.risk_manager import RiskManager, RiskConfig
from src.enhanced_data_v2 import EnhancedDataV2, get_enhanced_v2

logger = logging.getLogger(__name__)


class RiskManagerV2(RiskManager):
    """
    Extended Risk Manager dengan microstructure protection.
    
    Inherits all RiskManager functionality dan menambahkan:
    - Liquidation cascade blocking
    - Whale divergence detection  
    - Extreme sentiment filtering
    - Wall proximity warnings
    """
    
    def __init__(self, config: RiskConfig = None, db_path: str = "data/screener.db",
                 enhanced_data: Optional[EnhancedDataV2] = None,
                 microstructure_config: Optional[dict] = None):
        super().__init__(config, db_path)
        self.enhanced = enhanced_data
        
        # Load microstructure config
        micro_config = microstructure_config or {}
        liq_config = micro_config.get("liquidation", {})
        whale_config = micro_config.get("whale", {})
        risk_config = micro_config.get("risk", {})
        
        # Microstructure thresholds from config
        self.liq_cascade_threshold_usd = liq_config.get("cascade_warning_usd", 1000000)
        self.liq_cascade_block_usd = liq_config.get("cascade_block_usd", 3000000)
        self.whale_divergence_threshold = whale_config.get("position_flip_threshold", 0.1)
        self.extreme_sentiment_threshold = risk_config.get("extreme_sentiment_threshold", 75)
        self.wall_proximity_pct = micro_config.get("orderbook", {}).get("wall_proximity_pct", 1.5)
        
        logger.info("[RiskManagerV2] Initialized with microstructure protection")
    
    def can_trade(self, signal: Dict, market_data: Dict = None) -> Dict:
        """
        Enhanced can_trade dengan microstructure checks.
        
        Args:
            signal: Signal dict dengan symbol, price, sl, tp, etc.
            market_data: Optional market context (can include enhanced data)
            
        Returns:
            Extended result dengan microstructure warnings
        """
        # 1. Call parent can_trade untuk base checks
        base_result = super().can_trade(signal, market_data)
        
        # If already blocked by parent, return as-is
        if not base_result.get("allowed", True):
            return base_result
        
        # 2. Get microstructure data if not provided
        symbol = signal.get("symbol", "")
        current_price = signal.get("price", 0)
        micro = None
        
        if market_data and "microstructure" in market_data:
            micro = market_data["microstructure"]
        elif symbol and current_price > 0:
            try:
                micro = self.enhanced.get_full_microstructure(symbol, current_price)
            except Exception as e:
                logger.debug(f"[RiskManagerV2] Could not fetch microstructure for {symbol}: {e}")
        
        if not micro:
            # No microstructure data, return base result
            return base_result
        
        # 3. Check liquidation cascade (CIRCUIT BREAKER)
        liq_check = self._check_liquidation_cascade(micro, signal)
        if not liq_check["allowed"]:
            return {
                "allowed": False,
                "reason": liq_check["reason"],
                "risk_score": 5,
                "recommended_position": 0,
                "warnings": base_result.get("warnings", []) + [liq_check["warning"]]
            }
        
        # 4. Check whale divergence
        whale_check = self._check_whale_divergence(micro, signal)
        
        # 5. Check extreme sentiment
        sentiment_check = self._check_extreme_sentiment(micro, signal)
        
        # 6. Check order book walls
        wall_check = self._check_wall_proximity(micro, signal)
        
        # Combine all checks
        adjusted_score = base_result.get("risk_score", 50)
        all_warnings = base_result.get("warnings", [])
        
        # Apply score adjustments
        if whale_check["penalty"]:
            adjusted_score -= 15
            all_warnings.append(whale_check["warning"])
        
        if sentiment_check["penalty"]:
            adjusted_score -= 10
            all_warnings.append(sentiment_check["warning"])
        
        if wall_check["penalty"]:
            adjusted_score -= 5
            all_warnings.append(wall_check["warning"])
        
        # Clamp score
        adjusted_score = max(0, min(100, adjusted_score))
        
        # Recalculate position size dengan adjusted score
        base_position = base_result.get("recommended_position", 0.15)
        adjusted_position = base_position * (adjusted_score / max(base_result.get("risk_score", 50), 1))
        
        # Final decision
        allowed = adjusted_score >= 30  # Minimum 30 to trade
        
        return {
            "allowed": allowed,
            "reason": "OK" if allowed else "MICROSTRUCTURE_RISK_DETECTED",
            "risk_score": round(adjusted_score, 1),
            "recommended_position": round(adjusted_position, 3) if allowed else 0,
            "warnings": all_warnings,
            "microstructure": {
                "liquidation_pressure": liq_check.get("pressure", "neutral"),
                "whale_aligned": not whale_check["penalty"],
                "sentiment_extreme": sentiment_check["penalty"],
                "wall_nearby": wall_check["penalty"]
            }
        }
    
    def _check_liquidation_cascade(self, micro: Dict, signal: Dict) -> Dict:
        """
        Check if liquidation cascade is happening (BLOCK TRADES).
        
        Logic:
        - Heavy liquidations opposite to signal direction = wait for cascade to finish
        - $1M+ liquidations = warning
        - $3M+ liquidations = block (too dangerous)
        """
        liq = micro.get("liquidations", {})
        recent_value = liq.get("recent_value_usd", 0)
        pressure = liq.get("pressure", "neutral")
        signal_type = signal.get("signal", "WAIT")
        
        result = {
            "allowed": True,
            "reason": "",
            "warning": "",
            "pressure": pressure
        }
        
        # Block jika liquidations terlalu besar
        if recent_value >= self.liq_cascade_block_usd:
            if pressure == "long" and signal_type == "LONG":
                # Longs being liquidated, trying to go long = catching falling knife
                result["allowed"] = False
                result["reason"] = f"LIQUIDATION_CASCADE: ${recent_value:,.0f} long liquidations detected"
                result["warning"] = "Long cascade ongoing - wait for stabilization"
                logger.warning(f"[RiskManagerV2] BLOCKED {signal.get('symbol')} LONG - Cascade ${recent_value:,.0f}")
            elif pressure == "short" and signal_type == "SHORT":
                # Shorts being liquidated, trying to go short = short squeeze risk
                result["allowed"] = False
                result["reason"] = f"SHORT_SQUEEZE_RISK: ${recent_value:,.0f} short liquidations detected"
                result["warning"] = "Short cascade ongoing - potential squeeze"
                logger.warning(f"[RiskManagerV2] BLOCKED {signal.get('symbol')} SHORT - Squeeze ${recent_value:,.0f}")
        
        # Warning jika significant liquidations
        elif recent_value >= self.liq_cascade_threshold_usd:
            if pressure == "long":
                result["warning"] = f"Long liquidation warning: ${recent_value:,.0f} recently"
            elif pressure == "short":
                result["warning"] = f"Short liquidation warning: ${recent_value:,.0f} recently"
        
        return result
    
    def _check_whale_divergence(self, micro: Dict, signal: Dict) -> Dict:
        """
        Check if whales are positioned opposite to signal.
        
        Logic:
        - Signal LONG tapi whales heavily short = WARNING (divergence)
        - Signal LONG dan whales heavily long = OK (aligned)
        """
        wp = micro.get("whale_position", {})
        whale_long_ratio = wp.get("long_ratio", 0.5)
        signal_type = signal.get("signal", "WAIT")
        
        result = {
            "penalty": False,
            "warning": "",
            "aligned": True
        }
        
        if signal_type == "LONG":
            if whale_long_ratio < 0.3:  # Whales heavily short
                result["penalty"] = True
                result["aligned"] = False
                result["warning"] = f"WHALE_DIVERGENCE: Whales {whale_long_ratio:.0%} short vs your LONG"
            elif whale_long_ratio > 0.7:  # Whales aligned
                result["aligned"] = True
        
        elif signal_type == "SHORT":
            if whale_long_ratio > 0.7:  # Whales heavily long
                result["penalty"] = True
                result["aligned"] = False
                result["warning"] = f"WHALE_DIVERGENCE: Whales {whale_long_ratio:.0%} long vs your SHORT"
            elif whale_long_ratio < 0.3:  # Whales aligned short
                result["aligned"] = True
        
        return result
    
    def _check_extreme_sentiment(self, micro: Dict, signal: Dict) -> Dict:
        """
        Check if sentiment is extreme (contrarian warning).
        
        Logic:
        - Extreme bullish sentiment + LONG signal = WARNING (crowded trade)
        - Extreme bearish sentiment + SHORT signal = WARNING (crowded trade)
        """
        sentiment = micro.get("sentiment", "NEUTRAL")
        confidence = micro.get("confidence", 50)
        signal_type = signal.get("signal", "WAIT")
        
        result = {
            "penalty": False,
            "warning": ""
        }
        
        # Extreme bullish + LONG = contrarian warning
        if sentiment == "BULLISH" and confidence >= self.extreme_sentiment_threshold:
            if signal_type == "LONG":
                result["penalty"] = True
                result["warning"] = f"EXTREME_BULLISH_SENTIMENT: {confidence}% confidence - crowded long trade"
        
        # Extreme bearish + SHORT = contrarian warning
        elif sentiment == "BEARISH" and confidence >= self.extreme_sentiment_threshold:
            if signal_type == "SHORT":
                result["penalty"] = True
                result["warning"] = f"EXTREME_BEARISH_SENTIMENT: {confidence}% confidence - crowded short trade"
        
        return result
    
    def _check_wall_proximity(self, micro: Dict, signal: Dict) -> Dict:
        """
        Check if entering near order book wall.
        
        Logic:
        - LONG near resistance wall = wait for breakout
        - SHORT near support wall = wait for breakdown
        """
        ob = micro.get("order_book", {})
        signal_type = signal.get("signal", "WAIT")
        
        result = {
            "penalty": False,
            "warning": ""
        }
        
        support_dist = ob.get("support_distance_pct")
        resistance_dist = ob.get("resistance_distance_pct")
        
        if signal_type == "LONG":
            if resistance_dist is not None and resistance_dist < self.wall_proximity_pct:
                result["penalty"] = True
                result["warning"] = f"RESISTANCE_NEARBY: Strong wall {resistance_dist:.2f}% above entry"
        
        elif signal_type == "SHORT":
            if support_dist is not None and support_dist < self.wall_proximity_pct:
                result["penalty"] = True
                result["warning"] = f"SUPPORT_NEARBY: Strong wall {support_dist:.2f}% below entry"
        
        return result
    
    def detect_market_manipulation(self, symbol: str, current_price: float) -> Optional[Dict]:
        """
        Detect potential market manipulation (pump/dump signals).
        
        Warning signs:
        - Massive whale accumulation then sudden sell
        - Unusual liquidation patterns
        - Wall spoofing (large walls disappear)
        
        Returns warning jika manipulation detected.
        """
        try:
            micro = self.enhanced.get_full_microstructure(symbol, current_price)
        except Exception as e:
            logger.debug(f"[RiskManagerV2] Manipulation check failed for {symbol}: {e}")
            return None
        
        warnings = []
        manipulation_score = 0
        
        # Check 1: Whale dump after accumulation
        wf = micro.get("whale_flow", {})
        if wf.get("sell_value_usd", 0) > 1000000:  # $1M+ sell
            wp = micro.get("whale_position", {})
            if wp.get("long_ratio", 0.5) > 0.6:  # But still positioned long
                warnings.append("WHALE_DUMP_RISK: Selling while holding longs")
                manipulation_score += 30
        
        # Check 2: Unusual liquidation pattern (stop hunt)
        liq = micro.get("liquidations", {})
        if liq.get("recent_count", 0) > 50:  # 50+ liquidations recently
            if liq.get("pressure") == "long" and liq.get("short_liquidations_usd", 0) < 10000:
                warnings.append("STOP_HUNT_SUSPECTED: Heavy long liquidations with low short liq")
                manipulation_score += 40
        
        # Check 3: Extreme sentiment flip
        if micro.get("sentiment") == "BULLISH" and micro.get("confidence", 0) > 80:
            if wf.get("pressure") == "sell":
                warnings.append("SENTIMENT_MANIPULATION: Extreme bullish but whales selling")
                manipulation_score += 25
        
        if manipulation_score >= 50:
            return {
                "manipulation_detected": True,
                "score": manipulation_score,
                "warnings": warnings,
                "recommendation": "AVOID_TRADING"
            }
        elif manipulation_score >= 25:
            return {
                "manipulation_detected": False,
                "suspicious": True,
                "score": manipulation_score,
                "warnings": warnings,
                "recommendation": "REDUCE_SIZE"
            }
        
        return {"manipulation_detected": False, "score": 0}


# Singleton untuk easy access
_risk_manager_v2: Optional[RiskManagerV2] = None


def get_risk_manager_v2(config: RiskConfig = None, db_path: str = "data/screener.db",
                        enhanced_data: Optional[EnhancedDataV2] = None,
                        microstructure_config: Optional[dict] = None) -> RiskManagerV2:
    """Get or create RiskManagerV2 singleton."""
    global _risk_manager_v2
    if _risk_manager_v2 is None:
        _risk_manager_v2 = RiskManagerV2(config, db_path, enhanced_data, microstructure_config)
    return _risk_manager_v2
