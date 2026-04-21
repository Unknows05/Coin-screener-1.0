"""
Enhanced Market Data v2 — Real Liquidations, Whale Flow, Microstructure.
Optimized for 30 coins with intelligent caching and rate limiting.
"""
import logging
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from collections import deque
import requests

from src.binance_api import BinanceFuturesAPI

logger = logging.getLogger(__name__)


@dataclass
class LiquidationEvent:
    """Real liquidation event from forceOrders."""
    symbol: str
    price: float
    qty: float
    value_usd: float
    side: str  # 'SELL' = long liquidated, 'BUY' = short liquidated
    timestamp: datetime


@dataclass
class WhaleTrade:
    """Large trade detection."""
    symbol: str
    price: float
    qty: float
    value_usd: float
    side: str  # 'BUY' or 'SELL' from taker perspective
    timestamp: datetime
    is_buyer_maker: bool


@dataclass
class OrderBookWall:
    """Detected support/resistance wall."""
    price: float
    size: float
    side: str  # 'bid' or 'ask'
    strength: str  # 'weak', 'medium', 'strong'


class EnhancedDataV2:
    """
    High-performance enhanced data fetcher for Binance Futures.
    
    Features:
    - Real liquidation data (forceOrders)
    - Whale position ratio (size-weighted, not account-weighted)
    - Large trade detection (aggTrades)
    - Order book wall detection
    - Smart caching with TTL
    - Rate limiting compliance
    """
    
    def __init__(self, api: Optional[BinanceFuturesAPI] = None, 
                 config: Optional[dict] = None):
        self.api = api or BinanceFuturesAPI()
        
        # Load config atau use defaults
        micro_config = config or {}
        liq_config = micro_config.get("liquidation", {})
        whale_config = micro_config.get("whale", {})
        ob_config = micro_config.get("orderbook", {})
        
        self.cache_ttl = micro_config.get("cache_ttl_seconds", 60)
        self.whale_threshold = whale_config.get("trade_threshold_usd", 100000)
        self.liq_cascade_warning = liq_config.get("cascade_warning_usd", 1000000)
        self.liq_cascade_block = liq_config.get("cascade_block_usd", 3000000)
        self.liq_window = liq_config.get("window_minutes", 15)
        self.wall_threshold = ob_config.get("wall_threshold_usd", 500000)
        
        # Thread-safe cache
        self._cache: Dict[str, Tuple[any, float]] = {}
        self._cache_lock = threading.Lock()
        
        # Sliding window for whale detection
        self._whale_history: Dict[str, deque] = {}  # symbol -> deque of WhaleTrade
        self._liq_history: Dict[str, deque] = {}  # symbol -> deque of LiquidationEvent
        self._history_lock = threading.Lock()
        
        logger.info(f"[EnhancedV2] Initialized with {self.cache_ttl}s cache, ${self.whale_threshold:,.0f} whale threshold")
    
    def _get_cached(self, key: str) -> Optional[any]:
        """Get from cache if not expired."""
        with self._cache_lock:
            if key in self._cache:
                data, timestamp = self._cache[key]
                if time.time() - timestamp < self.cache_ttl:
                    return data
        return None
    
    def _set_cache(self, key: str, data: any):
        """Set cache with timestamp."""
        with self._cache_lock:
            self._cache[key] = (data, time.time())
    
    # =========================================================================
    # 1. REAL LIQUIDATION DATA (forceOrders)
    # =========================================================================
    
    def get_real_liquidations(self, symbol: str, limit: int = 100) -> List[LiquidationEvent]:
        """
        Fetch REAL liquidation data from forceOrders endpoint.
        
        Args:
            symbol: Trading pair (e.g., 'BTCUSDT')
            limit: Number of recent liquidations to fetch (max 1000)
            
        Returns:
            List of LiquidationEvent objects
        """
        cache_key = f"liq_{symbol}_{limit}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        
        try:
            # forceOrders returns real liquidation orders
            data = self.api._get("/fapi/v1/forceOrders", {
                "symbol": symbol.upper(),
                "limit": min(limit, 1000)
            })
            
            if not isinstance(data, list):
                return []
            
            liquidations = []
            for item in data:
                try:
                    liq = LiquidationEvent(
                        symbol=symbol,
                        price=float(item.get("price", 0)),
                        qty=float(item.get("qty", 0)),
                        value_usd=float(item.get("price", 0)) * float(item.get("qty", 0)),
                        side=item.get("side", ""),  # 'SELL' = long liq, 'BUY' = short liq
                        timestamp=datetime.fromtimestamp(item.get("time", 0) / 1000)
                    )
                    liquidations.append(liq)
                except (ValueError, TypeError) as e:
                    logger.debug(f"[EnhancedV2] Parse error for {symbol}: {e}")
                    continue
            
            # Update history
            with self._history_lock:
                if symbol not in self._liq_history:
                    self._liq_history[symbol] = deque(maxlen=1000)
                self._liq_history[symbol].extend(liquidations)
            
            self._set_cache(cache_key, liquidations)
            logger.debug(f"[EnhancedV2] Fetched {len(liquidations)} real liquidations for {symbol}")
            return liquidations
            
        except Exception as e:
            logger.warning(f"[EnhancedV2] Failed to fetch liquidations for {symbol}: {e}")
            return []
    
    def get_liquidation_summary(self, symbol: str, window_minutes: int = 15) -> Dict:
        """
        Get liquidation summary for a symbol over time window.
        
        Returns:
            {
                'total_count': int,
                'total_value_usd': float,
                'long_liquidations_usd': float,  # SELL side
                'short_liquidations_usd': float,  # BUY side
                'largest_liq_usd': float,
                'recent_count': int,  # in window
                'recent_value_usd': float,
                'pressure': str  # 'long', 'short', 'neutral'
            }
        """
        liquidations = self.get_real_liquidations(symbol, limit=100)
        
        if not liquidations:
            return {
                'total_count': 0,
                'total_value_usd': 0,
                'long_liquidations_usd': 0,
                'short_liquidations_usd': 0,
                'largest_liq_usd': 0,
                'recent_count': 0,
                'recent_value_usd': 0,
                'pressure': 'neutral'
            }
        
        cutoff = datetime.now() - timedelta(minutes=window_minutes)
        
        long_liq = sum(l.value_usd for l in liquidations if l.side == 'SELL')
        short_liq = sum(l.value_usd for l in liquidations if l.side == 'BUY')
        total_value = long_liq + short_liq
        
        recent = [l for l in liquidations if l.timestamp > cutoff]
        recent_value = sum(l.value_usd for l in recent)
        
        largest = max((l.value_usd for l in liquidations), default=0)
        
        # Determine pressure
        if long_liq > short_liq * 2:
            pressure = 'long'  # Longs being liquidated = short pressure
        elif short_liq > long_liq * 2:
            pressure = 'short'  # Shorts being liquidated = long pressure
        else:
            pressure = 'neutral'
        
        return {
            'total_count': len(liquidations),
            'total_value_usd': round(total_value, 2),
            'long_liquidations_usd': round(long_liq, 2),
            'short_liquidations_usd': round(short_liq, 2),
            'largest_liq_usd': round(largest, 2),
            'recent_count': len(recent),
            'recent_value_usd': round(recent_value, 2),
            'pressure': pressure
        }
    
    # =========================================================================
    # 2. WHALE POSITION RATIO (Position-weighted, NOT account-weighted)
    # =========================================================================
    
    def get_whale_position_ratio(self, symbol: str, period: str = "15m") -> Optional[Dict]:
        """
        Get top trader position ratio (SIZE-weighted, not account-weighted).
        
        This is BETTER than account ratio because:
        - Account ratio: 100 accounts with $100 each = 50% weight per side
        - Position ratio: 1 whale with $10M = dominates the ratio
        
        Args:
            symbol: Trading pair
            period: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
            
        Returns:
            {
                'longRatio': float,  # % of positions by SIZE (not accounts)
                'shortRatio': float,
                'timestamp': int
            }
        """
        cache_key = f"whale_pos_{symbol}_{period}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        
        try:
            data = self.api._get("/futures/data/topLongShortPositionRatio", {
                "symbol": symbol.upper(),
                "period": period
            })
            
            if isinstance(data, list) and len(data) > 0:
                latest = data[-1]
                result = {
                    'longRatio': float(latest.get('longAccount', 0)),  # Actually position ratio
                    'shortRatio': float(latest.get('shortAccount', 0)),
                    'timestamp': latest.get('timestamp', 0)
                }
                self._set_cache(cache_key, result)
                return result
            
            return None
            
        except Exception as e:
            logger.warning(f"[EnhancedV2] Failed to fetch whale position ratio for {symbol}: {e}")
            return None
    
    def detect_whale_flip(self, symbol: str, threshold: float = 0.1) -> Optional[str]:
        """
        Detect if whales have flipped position recently.
        
        Args:
            symbol: Trading pair
            threshold: Minimum ratio change to trigger flip detection (0.1 = 10%)
            
        Returns:
            'LONG_TO_SHORT', 'SHORT_TO_LONG', or None
        """
        try:
            # Get 5m data for recent history
            data = self.api._get("/futures/data/topLongShortPositionRatio", {
                "symbol": symbol.upper(),
                "period": "5m",
                "limit": 5
            })
            
            if not isinstance(data, list) or len(data) < 2:
                return None
            
            # Get first and last
            old = data[0]
            new = data[-1]
            
            old_long = float(old.get('longAccount', 0.5))
            new_long = float(new.get('longAccount', 0.5))
            
            change = new_long - old_long
            
            if change > threshold and old_long < 0.5 and new_long > 0.5:
                return 'SHORT_TO_LONG'
            elif change < -threshold and old_long > 0.5 and new_long < 0.5:
                return 'LONG_TO_SHORT'
            
            return None
            
        except Exception as e:
            logger.debug(f"[EnhancedV2] Flip detection failed for {symbol}: {e}")
            return None
    
    # =========================================================================
    # 3. LARGE TRADE DETECTION (aggTrades)
    # =========================================================================
    
    def get_large_trades(self, symbol: str, min_usd: Optional[float] = None, 
                         limit: int = 100) -> List[WhaleTrade]:
        """
        Detect large trades (whale activity) from aggTrades.
        
        Args:
            symbol: Trading pair
            min_usd: Minimum trade size in USD (default: self.whale_threshold)
            limit: Number of recent trades to check
            
        Returns:
            List of WhaleTrade objects above threshold
        """
        min_usd = min_usd or self.whale_threshold
        cache_key = f"whale_trades_{symbol}_{min_usd}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        
        try:
            # aggTrades gives individual trades aggregated
            data = self.api._get("/fapi/v1/aggTrades", {
                "symbol": symbol.upper(),
                "limit": min(limit, 1000)
            })
            
            if not isinstance(data, list):
                return []
            
            whale_trades = []
            for item in data:
                try:
                    price = float(item.get("p", 0))
                    qty = float(item.get("q", 0))
                    value_usd = price * qty
                    
                    if value_usd >= min_usd:
                        trade = WhaleTrade(
                            symbol=symbol,
                            price=price,
                            qty=qty,
                            value_usd=value_usd,
                            side='BUY' if not item.get("m", False) else 'SELL',  # !m = buyer is taker
                            timestamp=datetime.fromtimestamp(item.get("T", 0) / 1000),
                            is_buyer_maker=item.get("m", False)
                        )
                        whale_trades.append(trade)
                except (ValueError, TypeError):
                    continue
            
            # Update history
            with self._history_lock:
                if symbol not in self._whale_history:
                    self._whale_history[symbol] = deque(maxlen=1000)
                self._whale_history[symbol].extend(whale_trades)
            
            self._set_cache(cache_key, whale_trades)
            logger.debug(f"[EnhancedV2] Found {len(whale_trades)} whale trades for {symbol}")
            return whale_trades
            
        except Exception as e:
            logger.warning(f"[EnhancedV2] Failed to fetch aggTrades for {symbol}: {e}")
            return []
    
    def get_whale_flow_summary(self, symbol: str, window_minutes: int = 5) -> Dict:
        """
        Get whale flow summary (buy vs sell pressure from large trades).
        
        Returns:
            {
                'buy_count': int,
                'sell_count': int,
                'buy_value_usd': float,
                'sell_value_usd': float,
                'net_flow_usd': float,  # positive = buying, negative = selling
                'pressure': str  # 'buy', 'sell', 'neutral'
            }
        """
        trades = self.get_large_trades(symbol)
        
        cutoff = datetime.now() - timedelta(minutes=window_minutes)
        recent = [t for t in trades if t.timestamp > cutoff]
        
        buy_trades = [t for t in recent if t.side == 'BUY']
        sell_trades = [t for t in recent if t.side == 'SELL']
        
        buy_value = sum(t.value_usd for t in buy_trades)
        sell_value = sum(t.value_usd for t in sell_trades)
        net_flow = buy_value - sell_value
        
        # Determine pressure
        if buy_value > sell_value * 1.5:
            pressure = 'buy'
        elif sell_value > buy_value * 1.5:
            pressure = 'sell'
        else:
            pressure = 'neutral'
        
        return {
            'buy_count': len(buy_trades),
            'sell_count': len(sell_trades),
            'buy_value_usd': round(buy_value, 2),
            'sell_value_usd': round(sell_value, 2),
            'net_flow_usd': round(net_flow, 2),
            'pressure': pressure
        }
    
    # =========================================================================
    # 4. ORDER BOOK WALL DETECTION
    # =========================================================================
    
    def get_order_book_walls(self, symbol: str, depth: int = 500, 
                             wall_threshold_usd: float = 500000) -> List[OrderBookWall]:
        """
        Detect support/resistance walls in order book.
        
        Args:
            symbol: Trading pair
            depth: Order book depth to analyze
            wall_threshold_usd: Minimum USD size to qualify as wall
            
        Returns:
            List of OrderBookWall objects
        """
        cache_key = f"walls_{symbol}_{depth}_{wall_threshold_usd}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        
        try:
            data = self.api._get("/fapi/v1/depth", {
                "symbol": symbol.upper(),
                "limit": min(depth, 1000)
            })
            
            bids = data.get("bids", [])  # [price, qty]
            asks = data.get("asks", [])
            
            walls = []
            
            # Analyze bid walls (support)
            for price, qty in bids[:100]:  # Top 100 bids
                try:
                    price_f = float(price)
                    qty_f = float(qty)
                    value_usd = price_f * qty_f
                    
                    if value_usd >= wall_threshold_usd:
                        # Determine strength based on depth rank
                        strength = 'strong' if value_usd >= wall_threshold_usd * 2 else 'medium'
                        walls.append(OrderBookWall(
                            price=price_f,
                            size=value_usd,
                            side='bid',
                            strength=strength
                        ))
                except (ValueError, TypeError):
                    continue
            
            # Analyze ask walls (resistance)
            for price, qty in asks[:100]:  # Top 100 asks
                try:
                    price_f = float(price)
                    qty_f = float(qty)
                    value_usd = price_f * qty_f
                    
                    if value_usd >= wall_threshold_usd:
                        strength = 'strong' if value_usd >= wall_threshold_usd * 2 else 'medium'
                        walls.append(OrderBookWall(
                            price=price_f,
                            size=value_usd,
                            side='ask',
                            strength=strength
                        ))
                except (ValueError, TypeError):
                    continue
            
            # Sort by size
            walls.sort(key=lambda x: x.size, reverse=True)
            
            self._set_cache(cache_key, walls)
            logger.debug(f"[EnhancedV2] Found {len(walls)} walls for {symbol}")
            return walls
            
        except Exception as e:
            logger.warning(f"[EnhancedV2] Failed to analyze order book for {symbol}: {e}")
            return []
    
    def get_nearest_walls(self, symbol: str, current_price: float, 
                          wall_threshold_usd: float = 500000) -> Dict:
        """
        Get nearest support and resistance walls to current price.
        
        Returns:
            {
                'support': OrderBookWall or None,
                'resistance': OrderBookWall or None,
                'support_distance_pct': float,
                'resistance_distance_pct': float,
                'within_1pct': bool
            }
        """
        walls = self.get_order_book_walls(symbol, wall_threshold_usd=wall_threshold_usd)
        
        support_walls = [w for w in walls if w.side == 'bid' and w.price < current_price]
        resistance_walls = [w for w in walls if w.side == 'ask' and w.price > current_price]
        
        nearest_support = max(support_walls, key=lambda x: x.price) if support_walls else None
        nearest_resistance = min(resistance_walls, key=lambda x: x.price) if resistance_walls else None
        
        support_dist = ((current_price - nearest_support.price) / current_price * 100) if nearest_support else None
        resistance_dist = ((nearest_resistance.price - current_price) / current_price * 100) if nearest_resistance else None
        
        within_1pct = (
            (support_dist is not None and support_dist <= 1) or 
            (resistance_dist is not None and resistance_dist <= 1)
        )
        
        return {
            'support': nearest_support,
            'resistance': nearest_resistance,
            'support_distance_pct': round(support_dist, 2) if support_dist else None,
            'resistance_distance_pct': round(resistance_dist, 2) if resistance_dist else None,
            'within_1pct': within_1pct
        }
    
    # =========================================================================
    # 5. COMPOSITE METRICS (All-in-one for regime detection)
    # =========================================================================
    
    def get_full_microstructure(self, symbol: str, current_price: float) -> Dict:
        """
        Get complete market microstructure for a symbol.
        Optimized for regime detection and signal filtering.
        
        This is the main function to call during scanning.
        Efficiently combines all data sources with minimal API calls.
        
        Returns:
            {
                'symbol': str,
                'timestamp': datetime,
                
                # Liquidation data
                'liquidations': {
                    'recent_count': int,
                    'recent_value_usd': float,
                    'pressure': str,  # 'long', 'short', 'neutral'
                    'long_liq_usd': float,
                    'short_liq_usd': float
                },
                
                # Whale position (size-weighted)
                'whale_position': {
                    'long_ratio': float,
                    'short_ratio': float,
                    'flip_detected': str or None
                },
                
                # Whale flow (recent large trades)
                'whale_flow': {
                    'buy_value_usd': float,
                    'sell_value_usd': float,
                    'net_flow_usd': float,
                    'pressure': str  # 'buy', 'sell', 'neutral'
                },
                
                # Order book structure
                'order_book': {
                    'nearest_support': OrderBookWall or None,
                    'nearest_resistance': OrderBookWall or None,
                    'support_distance_pct': float,
                    'resistance_distance_pct': float,
                    'strong_walls_nearby': bool
                },
                
                # Composite signals
                'signals': List[str],  # e.g., ['LIQUIDATION_CASCADE_LONG', 'WHALE_ACCUMULATING']
                'sentiment': str,  # 'BULLISH', 'BEARISH', 'NEUTRAL'
                'confidence': int  # 0-100
            }
        """
        result = {
            'symbol': symbol,
            'timestamp': datetime.now(),
            'liquidations': {},
            'whale_position': {},
            'whale_flow': {},
            'order_book': {},
            'signals': [],
            'sentiment': 'NEUTRAL',
            'confidence': 50
        }
        
        # Fetch all data (with error handling for each)
        try:
            liq_summary = self.get_liquidation_summary(symbol)
            result['liquidations'] = liq_summary
        except Exception as e:
            logger.debug(f"[EnhancedV2] Liq summary failed for {symbol}: {e}")
        
        try:
            whale_pos = self.get_whale_position_ratio(symbol)
            if whale_pos:
                result['whale_position'] = {
                    'long_ratio': whale_pos.get('longRatio', 0.5),
                    'short_ratio': whale_pos.get('shortRatio', 0.5),
                    'flip_detected': self.detect_whale_flip(symbol)
                }
        except Exception as e:
            logger.debug(f"[EnhancedV2] Whale position failed for {symbol}: {e}")
        
        try:
            whale_flow = self.get_whale_flow_summary(symbol)
            result['whale_flow'] = whale_flow
        except Exception as e:
            logger.debug(f"[EnhancedV2] Whale flow failed for {symbol}: {e}")
        
        try:
            walls = self.get_nearest_walls(symbol, current_price)
            result['order_book'] = walls
        except Exception as e:
            logger.debug(f"[EnhancedV2] Order book failed for {symbol}: {e}")
        
        # Generate composite signals and sentiment
        signals = []
        sentiment_score = 50  # Neutral base
        
        # Liquidation signals
        liq = result['liquidations']
        if liq.get('recent_value_usd', 0) > 1000000:  # >$1M liquidated recently
            if liq.get('pressure') == 'long':
                signals.append('LIQUIDATION_CASCADE_LONG')  # Longs dying = potential long entry
                sentiment_score += 10
            elif liq.get('pressure') == 'short':
                signals.append('LIQUIDATION_CASCADE_SHORT')  # Shorts dying = potential short entry
                sentiment_score -= 10
        
        # Whale position signals
        wp = result['whale_position']
        long_ratio = wp.get('long_ratio', 0.5)
        flip = wp.get('flip_detected')
        
        if flip == 'SHORT_TO_LONG':
            signals.append('WHALE_FLIP_BULLISH')
            sentiment_score += 20
        elif flip == 'LONG_TO_SHORT':
            signals.append('WHALE_FLIP_BEARISH')
            sentiment_score -= 20
        elif long_ratio > 0.7:
            signals.append('WHALES_HEAVILY_LONG')
            sentiment_score += 15
        elif long_ratio < 0.3:
            signals.append('WHALES_HEAVILY_SHORT')
            sentiment_score -= 15
        
        # Whale flow signals
        wf = result['whale_flow']
        net_flow = wf.get('net_flow_usd', 0)
        flow_pressure = wf.get('pressure', 'neutral')
        
        if flow_pressure == 'buy' and abs(net_flow) > 500000:
            signals.append('WHALE_ACCUMULATING')
            sentiment_score += 10
        elif flow_pressure == 'sell' and abs(net_flow) > 500000:
            signals.append('WHALE_DISTRIBUTING')
            sentiment_score -= 10
        
        # Order book signals
        ob = result['order_book']
        if ob.get('strong_walls_nearby'):
            if ob.get('support_distance_pct', 100) < 1:
                signals.append('STRONG_SUPPORT_NEARBY')
            if ob.get('resistance_distance_pct', 100) < 1:
                signals.append('STRONG_RESISTANCE_NEARBY')
        
        result['signals'] = signals
        result['sentiment'] = 'BULLISH' if sentiment_score > 60 else 'BEARISH' if sentiment_score < 40 else 'NEUTRAL'
        result['confidence'] = min(100, max(0, abs(sentiment_score - 50) * 2))
        
        logger.debug(f"[EnhancedV2] Full microstructure for {symbol}: {len(signals)} signals, sentiment={result['sentiment']}")
        return result
    
    def clear_cache(self):
        """Clear all caches (useful for testing or memory management)."""
        with self._cache_lock:
            self._cache.clear()
        logger.info("[EnhancedV2] Cache cleared")


# Singleton instance for global access
_enhanced_v2: Optional[EnhancedDataV2] = None


def get_enhanced_v2(api: Optional[BinanceFuturesAPI] = None,
                    config: Optional[dict] = None) -> EnhancedDataV2:
    """Get or create EnhancedDataV2 singleton."""
    global _enhanced_v2
    if _enhanced_v2 is None:
        _enhanced_v2 = EnhancedDataV2(api, config)
    return _enhanced_v2
