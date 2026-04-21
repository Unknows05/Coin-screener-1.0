"""
Regime Detector v2 — Microstructure-Based Market Regime Detection.

Uses real liquidation data, whale positioning, and order flow untuk deteksi cepat.
Much faster response than price-only detection (hours vs days).
"""
import pandas as pd
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from src.regime import RegimeDetector  # Import v1 for fallback
from src.enhanced_data_v2 import EnhancedDataV2, get_enhanced_v2

logger = logging.getLogger(__name__)


class RegimeType(Enum):
    """Extended regime types with microstructure insights."""
    BULL = "BULL"
    BEAR = "BEAR"
    SIDEWAYS = "SIDEWAYS"
    HIGH_VOL = "HIGH_VOL"
    # New microstructure-based regimes
    LIQUIDATION_CASCADE = "LIQUIDATION_CASCADE"  # Heavy liquidations = reversal
    WHALE_ACCUMULATION = "WHALE_ACCUMULATION"    # Whales buying = early bull
    WHALE_DISTRIBUTION = "WHALE_DISTRIBUTION"      # Whales selling = early bear
    EXHAUSTION_RISK = "EXHAUSTION_RISK"            # Overleveraged = contrarian
    BREAKOUT_IMMINENT = "BREAKOUT_IMMINENT"        # Walls being eaten


@dataclass
class RegimeV2Result:
    """Extended regime result dengan microstructure insights."""
    regime: str
    confidence: float
    strength: str  # 'weak', 'moderate', 'strong'
    
    # Microstructure evidence
    signals: List[str]
    evidence: Dict
    
    # Trading recommendation
    trade_direction: str  # 'LONG', 'SHORT', 'WAIT'
    urgency: str  # 'immediate', 'soon', 'patience'
    
    # Timestamp
    timestamp: datetime


class RegimeDetectorV2:
    """
    Advanced regime detector menggunakan microstructure data.
    
    Key improvements vs v1:
    - Detects regime dalam MINUTES (not hours)
    - Uses real liquidations (not estimates)
    - Tracks whale positioning (size-weighted)
    - Identifies exhaustion/accumulation early
    """
    
    def __init__(self, enhanced_data: Optional[EnhancedDataV2] = None,
                 v1_detector=None, 
                 enable_microstructure: bool = True):
        self.enhanced = enhanced_data or get_enhanced_v2()
        self.v1 = v1_detector or RegimeDetector()
        self.enable_microstructure = enable_microstructure
        
        # Thresholds (tunable)
        self.liq_threshold_usd = 500000  # $500K for significant liquidation
        self.whale_flip_threshold = 0.1  # 10% change
        self.wall_proximity_pct = 1.5  # 1.5% distance
        
        logger.info(f"[RegimeV2] Initialized with microstructure={enable_microstructure}")
    
    def detect(self, df: pd.DataFrame, symbol: str, current_price: float) -> RegimeV2Result:
        """
        Detect market regime menggunakan microstructure + price data.
        
        Args:
            df: OHLCV DataFrame (untuk price-based detection fallback)
            symbol: Trading pair (e.g., 'BTCUSDT')
            current_price: Current market price
            
        Returns:
            RegimeV2Result dengan full analysis
        """
        # 1. Get v1 price-based regime (fallback foundation)
        v1_result = self.v1.detect(df)
        base_regime = v1_result.get("regime", "SIDEWAYS")
        base_confidence = v1_result.get("confidence", 0.5)
        
        # 2. Get microstructure data (jika enabled)
        micro = None
        if self.enable_microstructure:
            try:
                micro = self.enhanced.get_full_microstructure(symbol, current_price)
            except Exception as e:
                logger.warning(f"[RegimeV2] Microstructure fetch failed for {symbol}: {e}")
        
        # 3. Analyze microstructure signals
        micro_signals = []
        micro_evidence = {}
        
        if micro:
            # Liquidation analysis
            liq = micro.get('liquidations', {})
            if liq.get('recent_value_usd', 0) >= self.liq_threshold_usd:
                pressure = liq.get('pressure', 'neutral')
                liq_value = liq.get('recent_value_usd', 0)
                
                if pressure == 'long':
                    micro_signals.append('LONG_LIQUIDATION_CASCADE')
                    micro_evidence['long_liquidations_usd'] = liq_value
                    # Longs being liquidated = potential bottom
                elif pressure == 'short':
                    micro_signals.append('SHORT_LIQUIDATION_CASCADE')
                    micro_evidence['short_liquidations_usd'] = liq_value
                    # Shorts being liquidated = potential top
            
            # Whale position analysis
            wp = micro.get('whale_position', {})
            long_ratio = wp.get('long_ratio', 0.5)
            flip = wp.get('flip_detected')
            
            if flip == 'SHORT_TO_LONG':
                micro_signals.append('WHALE_FLIP_BULLISH')
                micro_evidence['whale_long_ratio'] = long_ratio
                micro_evidence['whale_flip'] = flip
            elif flip == 'LONG_TO_SHORT':
                micro_signals.append('WHALE_FLIP_BEARISH')
                micro_evidence['whale_long_ratio'] = long_ratio
                micro_evidence['whale_flip'] = flip
            elif long_ratio > 0.7:
                micro_signals.append('WHALES_HEAVILY_LONG')
                micro_evidence['whale_long_ratio'] = long_ratio
            elif long_ratio < 0.3:
                micro_signals.append('WHALES_HEAVILY_SHORT')
                micro_evidence['whale_long_ratio'] = long_ratio
            
            # Whale flow analysis
            wf = micro.get('whale_flow', {})
            net_flow = wf.get('net_flow_usd', 0)
            flow_pressure = wf.get('pressure', 'neutral')
            
            if flow_pressure == 'buy' and abs(net_flow) > 500000:
                micro_signals.append('WHALE_ACCUMULATING')
                micro_evidence['whale_net_flow_usd'] = net_flow
            elif flow_pressure == 'sell' and abs(net_flow) > 500000:
                micro_signals.append('WHALE_DISTRIBUTING')
                micro_evidence['whale_net_flow_usd'] = net_flow
            
            # Order book analysis
            ob = micro.get('order_book', {})
            support_dist = ob.get('support_distance_pct')
            resistance_dist = ob.get('resistance_distance_pct')
            
            if support_dist is not None and support_dist < self.wall_proximity_pct:
                micro_evidence['near_support_wall'] = True
                micro_evidence['support_distance_pct'] = support_dist
            
            if resistance_dist is not None and resistance_dist < self.wall_proximity_pct:
                micro_evidence['near_resistance_wall'] = True
                micro_evidence['resistance_distance_pct'] = resistance_dist
        
        # 4. Combine price + microstructure untuk final regime
        final_regime, final_confidence, strength = self._combine_analysis(
            base_regime, base_confidence, micro_signals, micro_evidence
        )
        
        # 5. Generate trading recommendation
        trade_dir, urgency = self._generate_recommendation(
            final_regime, final_confidence, micro_signals, micro_evidence
        )
        
        return RegimeV2Result(
            regime=final_regime,
            confidence=round(final_confidence, 2),
            strength=strength,
            signals=micro_signals,
            evidence=micro_evidence,
            trade_direction=trade_dir,
            urgency=urgency,
            timestamp=datetime.now()
        )
    
    def _combine_analysis(self, base_regime: str, base_confidence: float,
                         micro_signals: List[str], evidence: Dict) -> Tuple[str, float, str]:
        """
        Combine price-based dan microstructure analysis.
        
        Logic:
        - Microstructure can OVERRIDE price regime (early detection)
        - High confidence micro signals beat low confidence price signals
        - Multiple confirming signals increase confidence
        """
        
        # Score microstructure conviction
        micro_score = 0
        override_signals = []
        
        for signal in micro_signals:
            if signal == 'WHALE_FLIP_BULLISH':
                micro_score += 40
                override_signals.append('bull_override')
            elif signal == 'WHALE_FLIP_BEARISH':
                micro_score -= 40
                override_signals.append('bear_override')
            elif signal == 'WHALE_ACCUMULATING':
                micro_score += 25
            elif signal == 'WHALE_DISTRIBUTING':
                micro_score -= 25
            elif signal == 'LONG_LIQUIDATION_CASCADE':
                micro_score += 20  # Contrarian: longs dying = long opportunity
            elif signal == 'SHORT_LIQUIDATION_CASCADE':
                micro_score -= 20  # Contrarian: shorts dying = short opportunity
            elif signal == 'WHALES_HEAVILY_LONG':
                micro_score += 15
            elif signal == 'WHALES_HEAVILY_SHORT':
                micro_score -= 15
        
        # Determine if microstructure should override
        strong_micro = abs(micro_score) >= 30
        weak_price = base_confidence < 0.6
        
        if strong_micro and (weak_price or override_signals):
            # Microstructure override
            if micro_score > 0:
                final_regime = 'BULL'
            elif micro_score < 0:
                final_regime = 'BEAR'
            else:
                final_regime = base_regime
            
            # Confidence from micro strength
            final_confidence = min(0.95, 0.5 + abs(micro_score) / 100)
            
            # Determine strength
            if abs(micro_score) >= 50:
                strength = 'strong'
            elif abs(micro_score) >= 30:
                strength = 'moderate'
            else:
                strength = 'weak'
        else:
            # Price-based with micro adjustment
            final_regime = base_regime
            
            # Adjust confidence based on micro agreement/disagreement
            agreement = 0
            if base_regime == 'BULL' and micro_score > 0:
                agreement = micro_score
            elif base_regime == 'BEAR' and micro_score < 0:
                agreement = abs(micro_score)
            elif base_regime == 'BULL' and micro_score < 0:
                agreement = micro_score  # Negative = disagreement
            elif base_regime == 'BEAR' and micro_score > 0:
                agreement = -micro_score  # Negative = disagreement
            
            confidence_adjustment = agreement / 200  # ±0.25 max adjustment
            final_confidence = max(0.3, min(0.95, base_confidence + confidence_adjustment))
            
            # Strength from combined signals
            if final_confidence >= 0.8:
                strength = 'strong'
            elif final_confidence >= 0.6:
                strength = 'moderate'
            else:
                strength = 'weak'
        
        return final_regime, final_confidence, strength
    
    def _generate_recommendation(self, regime: str, confidence: float,
                                signals: List[str], evidence: Dict) -> Tuple[str, str]:
        """
        Generate trading recommendation dari analysis.
        
        Returns:
            (trade_direction, urgency)
        """
        # Default
        trade_dir = 'WAIT'
        urgency = 'patience'
        
        # Strong signals override
        if 'WHALE_FLIP_BULLISH' in signals and confidence > 0.7:
            trade_dir = 'LONG'
            urgency = 'immediate'
        elif 'WHALE_FLIP_BEARISH' in signals and confidence > 0.7:
            trade_dir = 'SHORT'
            urgency = 'immediate'
        elif 'WHALE_ACCUMULATING' in signals and confidence > 0.6:
            trade_dir = 'LONG'
            urgency = 'soon'
        elif 'WHALE_DISTRIBUTING' in signals and confidence > 0.6:
            trade_dir = 'SHORT'
            urgency = 'soon'
        elif 'LONG_LIQUIDATION_CASCADE' in signals:
            # Wait for cascade to finish
            trade_dir = 'LONG'
            urgency = 'soon'  # Enter after cascade
        elif 'SHORT_LIQUIDATION_CASCADE' in signals:
            trade_dir = 'SHORT'
            urgency = 'soon'
        else:
            # Standard regime-based
            if regime == 'BULL' and confidence > 0.65:
                trade_dir = 'LONG'
                urgency = 'soon'
            elif regime == 'BEAR' and confidence > 0.65:
                trade_dir = 'SHORT'
                urgency = 'soon'
            elif regime == 'HIGH_VOL':
                trade_dir = 'WAIT'
                urgency = 'patience'
        
        # Check for walls (enter dengan hati-hati kalau dekat wall)
        if evidence.get('near_resistance_wall') and trade_dir == 'LONG':
            urgency = 'patience'  # Wait for wall break
        if evidence.get('near_support_wall') and trade_dir == 'SHORT':
            urgency = 'patience'  # Wait for wall break
        
        return trade_dir, urgency
    
    def detect_market_flip(self, symbol: str, current_price: float,
                          lookback_minutes: int = 30) -> Optional[Dict]:
        """
        Detect if market is about to flip (early warning system).
        
        This is the key function untuk prevent Apr 20 disasters.
        
        Returns:
            {
                'flip_detected': bool,
                'direction': 'BULL_TO_BEAR' or 'BEAR_TO_BULL',
                'confidence': float,
                'evidence': List[str],
                'urgency': str
            }
        """
        try:
            micro = self.enhanced.get_full_microstructure(symbol, current_price)
        except Exception as e:
            logger.warning(f"[RegimeV2] Flip detection failed for {symbol}: {e}")
            return None
        
        evidence = []
        confidence = 0
        direction = None
        
        # Check whale flip
        wp = micro.get('whale_position', {})
        if wp.get('flip_detected') == 'SHORT_TO_LONG':
            evidence.append('Whales flipped LONG')
            confidence += 30
            direction = 'BEAR_TO_BULL'
        elif wp.get('flip_detected') == 'LONG_TO_SHORT':
            evidence.append('Whales flipped SHORT')
            confidence += 30
            direction = 'BULL_TO_BEAR'
        
        # Check liquidation cascade ending
        liq = micro.get('liquidations', {})
        if liq.get('pressure') == 'long' and liq.get('recent_value_usd', 0) > 1000000:
            evidence.append('Long liquidation cascade (potential bottom)')
            confidence += 20
            if direction is None:
                direction = 'BEAR_TO_BULL'
        elif liq.get('pressure') == 'short' and liq.get('recent_value_usd', 0) > 1000000:
            evidence.append('Short liquidation cascade (potential top)')
            confidence += 20
            if direction is None:
                direction = 'BULL_TO_BEAR'
        
        # Check whale flow divergence
        wf = micro.get('whale_flow', {})
        sentiment = micro.get('sentiment', 'NEUTRAL')
        
        if wf.get('pressure') == 'buy' and sentiment == 'BEARISH':
            evidence.append('Whales buying into bearish sentiment')
            confidence += 25
            if direction is None:
                direction = 'BEAR_TO_BULL'
        elif wf.get('pressure') == 'sell' and sentiment == 'BULLISH':
            evidence.append('Whales selling into bullish sentiment')
            confidence += 25
            if direction is None:
                direction = 'BULL_TO_BEAR'
        
        if confidence >= 40 and direction:
            return {
                'flip_detected': True,
                'direction': direction,
                'confidence': min(confidence / 100, 0.95),
                'evidence': evidence,
                'urgency': 'immediate' if confidence >= 60 else 'soon'
            }
        
        return {'flip_detected': False}


# Singleton
_regime_v2: Optional[RegimeDetectorV2] = None


def get_regime_v2(enhanced_data: Optional[EnhancedDataV2] = None) -> RegimeDetectorV2:
    """Get or create RegimeDetectorV2 singleton."""
    global _regime_v2
    if _regime_v2 is None:
        _regime_v2 = RegimeDetectorV2(enhanced_data)
    return _regime_v2
