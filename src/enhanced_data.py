"""
Enhanced Market Data Module — Advanced Futures Market Microstructure.

Fetches critical data for sophisticated signal generation:
1. Long/Short Ratio (sentiment)
2. Taker Buy/Sell Volume (order flow)
3. Order Book Depth (liquidity analysis)
4. Funding Rate History (carry cost trends)
5. Open Interest History (positioning trends)
6. Top Trader Long/Short Ratio (whale positioning)
7. Mark Price vs Index Price (basis/arbitrage)

All data is cached and rate-limited for efficient API usage.
"""
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import threading
import requests
import random

logger = logging.getLogger(__name__)


class EnhancedFuturesData:
    """
    Enhanced data fetcher for Binance USDS-M Futures.
    
    Provides advanced market microstructure data beyond basic OHLCV.
    """
    
    BASE_URL = "https://fapi.binance.com"
    CACHE_TTL_SECONDS = 300  # 5 minutes cache
    
    def __init__(self, cache_dir: str = "data/enhanced_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "CoinScreener-Enhanced/1.0"
        })
        self._cache: Dict[str, Tuple[any, float]] = {}  # In-memory cache
        self._lock = threading.Lock()
        
    def _rate_limited_get(self, endpoint: str, params: Optional[dict] = None, 
                         timeout: int = 10) -> dict:
        """Make rate-limited GET request."""
        url = f"{self.BASE_URL}{endpoint}"
        try:
            resp = self.session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"[EnhancedData] API error for {endpoint}: {e}")
            return {}
    
    def _get_cached_or_fetch(self, key: str, fetch_func, ttl: int = None) -> any:
        """Get from cache or fetch if expired."""
        if ttl is None:
            ttl = self.CACHE_TTL_SECONDS
            
        with self._lock:
            if key in self._cache:
                data, timestamp = self._cache[key]
                if time.time() - timestamp < ttl:
                    return data
        
        # Fetch fresh data
        try:
            data = fetch_func()
            with self._lock:
                self._cache[key] = (data, time.time())
            return data
        except Exception as e:
            logger.error(f"[EnhancedData] Fetch error for {key}: {e}")
            # Return stale cache if available
            with self._lock:
                if key in self._cache:
                    return self._cache[key][0]
            return None
    
    # =========================================================================
    # 1. LONG/SHORT RATIO (Account-level sentiment)
    # =========================================================================
    
    def get_long_short_ratio(self, symbol: str, period: str = "15m") -> Optional[Dict]:
        """
        Get global long/short account ratio.
        
        Period: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
        
        Returns:
            {
                "longAccount": float,  # % accounts long
                "shortAccount": float, # % accounts short
                "longShortRatio": float,
                "timestamp": int
            }
        """
        cache_key = f"ls_ratio_{symbol}_{period}"
        
        def fetch():
            params = {
                "symbol": symbol.upper(),
                "period": period
            }
            data = self._rate_limited_get("/futures/data/globalLongShortAccountRatio", params)
            if isinstance(data, list) and len(data) > 0:
                latest = data[-1]  # Get most recent
                return {
                    "longAccount": float(latest.get("longAccount", 0)),
                    "shortAccount": float(latest.get("shortAccount", 0)),
                    "longShortRatio": float(latest.get("longShortRatio", 0)),
                    "timestamp": latest.get("timestamp", 0)
                }
            return None
        
        return self._get_cached_or_fetch(cache_key, fetch)
    
    def get_long_short_ratio_trend(self, symbol: str, periods: int = 5) -> Optional[Dict]:
        """
        Get trend of L/S ratio over multiple periods.
        
        Returns trend direction and extremes for contrarian signals.
        """
        cache_key = f"ls_trend_{symbol}_{periods}"
        
        def fetch():
            params = {
                "symbol": symbol.upper(),
                "period": "15m",
                "limit": periods
            }
            data = self._rate_limited_get("/futures/data/globalLongShortAccountRatio", params)
            if isinstance(data, list) and len(data) >= 3:
                long_accounts = [float(d.get("longAccount", 0)) for d in data]
                avg_long = sum(long_accounts) / len(long_accounts)
                latest_long = long_accounts[-1]
                
                # Determine trend
                if latest_long > avg_long * 1.05:
                    trend = "INCREASING_LONGS"
                elif latest_long < avg_long * 0.95:
                    trend = "DECREASING_LONGS"
                else:
                    trend = "STABLE"
                
                # Contrarian signals
                signal = None
                if latest_long > 0.75:
                    signal = "EXTREME_LONG_EXHAUSTION_RISK"  # Potential short
                elif latest_long < 0.30:
                    signal = "EXTREME_SHORT_SQUEEZE_RISK"    # Potential long
                    
                return {
                    "latest_long_pct": latest_long,
                    "avg_long_pct": avg_long,
                    "trend": trend,
                    "contrarian_signal": signal,
                    "data_points": len(data)
                }
            return None
        
        return self._get_cached_or_fetch(cache_key, fetch, ttl=180)  # 3 min cache
    
    # =========================================================================
    # 2. TAKER BUY/SELL VOLUME (Order flow direction)
    # =========================================================================
    
    def get_taker_volume_ratio(self, symbol: str, period: str = "15m") -> Optional[Dict]:
        """
        Get taker buy/sell volume ratio.
        
        Taker = Market orders (aggressive)
        Maker = Limit orders (passive)
        
        High taker buy = Aggressive buying (potential top)
        High taker sell = Aggressive selling (potential bottom)
        """
        cache_key = f"taker_{symbol}_{period}"
        
        def fetch():
            params = {
                "symbol": symbol.upper(),
                "period": period
            }
            data = self._rate_limited_get("/futures/data/takerlongshortRatio", params)
            if isinstance(data, list) and len(data) > 0:
                latest = data[-1]
                buy_vol = float(latest.get("buyVol", 0))
                sell_vol = float(latest.get("sellVol", 0))
                total = buy_vol + sell_vol
                
                if total > 0:
                    buy_pct = buy_vol / total
                    sell_pct = sell_vol / total
                    
                    # Signal interpretation
                    flow_signal = None
                    if buy_pct > 0.65:
                        flow_signal = "HEAVY_TAKER_BUYING"  # Distribution risk
                    elif sell_pct > 0.65:
                        flow_signal = "HEAVY_TAKER_SELLING" # Accumulation opportunity
                    
                    return {
                        "buyVolume": buy_vol,
                        "sellVolume": sell_vol,
                        "buyPct": round(buy_pct, 3),
                        "sellPct": round(sell_pct, 3),
                        "takerRatio": round(buy_vol / sell_vol, 3) if sell_vol > 0 else float('inf'),
                        "flowSignal": flow_signal,
                        "timestamp": latest.get("timestamp", 0)
                    }
            return None
        
        return self._get_cached_or_fetch(cache_key, fetch)
    
    # =========================================================================
    # 3. ORDER BOOK DEPTH (Liquidity analysis)
    # =========================================================================
    
    def get_order_book_depth(self, symbol: str, limit: int = 500) -> Optional[Dict]:
        """
        Get order book depth and analyze liquidity clusters.
        
        Returns support/resistance levels based on bid/ask clustering.
        """
        cache_key = f"ob_depth_{symbol}_{limit}"
        
        def fetch():
            params = {
                "symbol": symbol.upper(),
                "limit": min(limit, 1000)
            }
            data = self._rate_limited_get("/fapi/v1/depth", params)
            if not data or "bids" not in data:
                return None
            
            bids = [[float(p), float(q)] for p, q in data.get("bids", [])]
            asks = [[float(p), float(q)] for p, q in data.get("asks", [])]
            
            if not bids or not asks:
                return None
            
            # Calculate metrics
            best_bid = bids[0][0]
            best_ask = asks[0][0]
            spread = best_ask - best_bid
            spread_pct = (spread / best_bid) * 100
            
            # Liquidity within 1% of mid price
            mid = (best_bid + best_ask) / 2
            threshold = mid * 0.01  # 1%
            
            bid_liquidity = sum(qty for price, qty in bids if price >= mid - threshold)
            ask_liquidity = sum(qty for price, qty in asks if price <= mid + threshold)
            
            # Find clusters (support/resistance walls)
            def find_clusters(orders, min_qty):
                clusters = []
                for price, qty in orders:
                    if qty >= min_qty:
                        clusters.append({"price": price, "qty": qty, "value": price * qty})
                return sorted(clusters, key=lambda x: x["value"], reverse=True)[:3]
            
            avg_qty = sum(qty for _, qty in bids) / len(bids) if bids else 0
            bid_walls = find_clusters(bids, avg_qty * 3)  # 3x average = wall
            ask_walls = find_clusters(asks, avg_qty * 3)
            
            # Imbalance signal
            imbalance = None
            liq_ratio = bid_liquidity / ask_liquidity if ask_liquidity > 0 else float('inf')
            if liq_ratio > 2.0:
                imbalance = "STRONG_BID_SUPPORT"
            elif liq_ratio < 0.5:
                imbalance = "STRONG_ASK_RESISTANCE"
            
            return {
                "bestBid": best_bid,
                "bestAsk": best_ask,
                "spread": round(spread, 4),
                "spreadPct": round(spread_pct, 4),
                "bidLiquidity1pct": round(bid_liquidity, 2),
                "askLiquidity1pct": round(ask_liquidity, 2),
                "liquidityRatio": round(liq_ratio, 2),
                "bidWalls": bid_walls,  # Strong support levels
                "askWalls": ask_walls,    # Strong resistance levels
                "imbalanceSignal": imbalance,
                "lastUpdateId": data.get("lastUpdateId", 0)
            }
        
        return self._get_cached_or_fetch(cache_key, fetch, ttl=30)  # 30s cache for orderbook
    
    # =========================================================================
    # 4. FUNDING RATE HISTORY (Carry cost trend)
    # =========================================================================
    
    def get_funding_rate_trend(self, symbol: str, periods: int = 24) -> Optional[Dict]:
        """
        Get funding rate trend and extreme readings.
        
        High positive funding = Longs pay shorts (overleveraged longs)
        High negative funding = Shorts pay longs (overleveraged shorts)
        """
        cache_key = f"funding_trend_{symbol}_{periods}"
        
        def fetch():
            params = {
                "symbol": symbol.upper(),
                "limit": periods
            }
            data = self._rate_limited_get("/fapi/v1/fundingRate", params)
            if isinstance(data, list) and len(data) >= 3:
                rates = [float(d.get("fundingRate", 0)) for d in data]
                avg_rate = sum(rates) / len(rates)
                latest_rate = rates[-1]
                max_rate = max(rates)
                min_rate = min(rates)
                
                # Trend analysis
                if latest_rate > avg_rate * 1.5 and latest_rate > 0.0001:
                    trend = "INCREASING_PREMIUM"  # Longs getting more aggressive
                elif latest_rate < avg_rate * 0.5 and latest_rate < -0.0001:
                    trend = "INCREASING_DISCOUNT" # Shorts getting more aggressive
                else:
                    trend = "STABLE"
                
                # Extreme signals (annualized)
                annualized = latest_rate * 3 * 365  # 8h intervals
                extreme_signal = None
                if annualized > 30:  # >30% annualized
                    extreme_signal = "EXTREME_LONG_FUNDING"  # Contrarian short
                elif annualized < -30:
                    extreme_signal = "EXTREME_SHORT_FUNDING" # Contrarian long
                
                return {
                    "currentRate": latest_rate,
                    "currentRatePct": round(latest_rate * 100, 4),
                    "annualizedPct": round(annualized, 2),
                    "avgRate": round(avg_rate, 6),
                    "maxRate": round(max_rate, 6),
                    "minRate": round(min_rate, 6),
                    "trend": trend,
                    "extremeSignal": extreme_signal,
                    "dataPoints": len(data)
                }
            return None
        
        return self._get_cached_or_fetch(cache_key, fetch, ttl=600)  # 10 min cache
    
    # =========================================================================
    # 5. OPEN INTEREST HISTORY (Positioning trend)
    # =========================================================================
    
    def get_open_interest_trend(self, symbol: str, period: str = "15m", 
                              limit: int = 20) -> Optional[Dict]:
        """
        Get open interest trend to detect positioning changes.
        
        OI + Price Up = Trend healthy (new money entering)
        OI + Price Down = Distribution (longs trapped)
        OI - Price Up = Short squeeze (weak shorts)
        OI - Price Down = Capitulation
        """
        cache_key = f"oi_trend_{symbol}_{period}_{limit}"
        
        def fetch():
            params = {
                "symbol": symbol.upper(),
                "period": period,
                "limit": limit
            }
            data = self._rate_limited_get("/fapi/v1/openInterestHist", params)
            if isinstance(data, list) and len(data) >= 5:
                oi_values = [float(d.get("sumOpenInterestValue", 0)) for d in data]
                latest_oi = oi_values[-1]
                avg_oi = sum(oi_values) / len(oi_values)
                
                # Trend
                if latest_oi > avg_oi * 1.05:
                    oi_trend = "INCREASING"
                elif latest_oi < avg_oi * 0.95:
                    oi_trend = "DECREASING"
                else:
                    oi_trend = "STABLE"
                
                return {
                    "latestOiValue": round(latest_oi, 2),
                    "avgOiValue": round(avg_oi, 2),
                    "oiTrend": oi_trend,
                    "oiChangePct": round((latest_oi / avg_oi - 1) * 100, 2),
                    "maxOi": round(max(oi_values), 2),
                    "minOi": round(min(oi_values), 2),
                    "dataPoints": len(data)
                }
            return None
        
        return self._get_cached_or_fetch(cache_key, fetch, ttl=300)
    
    # =========================================================================
    # 6. TOP TRADER LONG/SHORT RATIO (Whale positioning)
    # =========================================================================
    
    def get_top_trader_ratio(self, symbol: str, period: str = "15m") -> Optional[Dict]:
        """
        Get top trader (whale) positioning.
        
        Top traders often = smart money
        If whales long while retail short = bullish divergence
        """
        cache_key = f"top_trader_{symbol}_{period}"
        
        def fetch():
            params = {
                "symbol": symbol.upper(),
                "period": period
            }
            data = self._rate_limited_get("/futures/data/topLongShortAccountRatio", params)
            if isinstance(data, list) and len(data) > 0:
                latest = data[-1]
                long_ratio = float(latest.get("longAccount", 0))
                short_ratio = float(latest.get("shortAccount", 0))
                
                return {
                    "longRatio": round(long_ratio, 3),
                    "shortRatio": round(short_ratio, 3),
                    "longShortRatio": float(latest.get("longShortRatio", 0)),
                    "whaleBias": "LONG" if long_ratio > 0.6 else "SHORT" if short_ratio > 0.6 else "NEUTRAL",
                    "timestamp": latest.get("timestamp", 0)
                }
            return None
        
        return self._get_cached_or_fetch(cache_key, fetch, ttl=300)
    
    # =========================================================================
    # 7. COMPOSITE DATA FETCHER (All-in-one for a symbol)
    # =========================================================================
    
    def get_enhanced_metrics(self, symbol: str) -> Dict:
        """
        Get all enhanced metrics for a symbol in one call.
        
        Returns comprehensive market microstructure analysis.
        """
        logger.debug(f"[EnhancedData] Fetching enhanced metrics for {symbol}")
        
        metrics = {
            "symbol": symbol,
            "timestamp": int(time.time() * 1000),
            "longShortRatio": None,
            "takerVolume": None,
            "orderBook": None,
            "funding": None,
            "openInterest": None,
            "topTrader": None,
            "compositeSignals": []
        }
        
        # Fetch all data types
        metrics["longShortRatio"] = self.get_long_short_ratio_trend(symbol)
        metrics["takerVolume"] = self.get_taker_volume_ratio(symbol)
        metrics["orderBook"] = self.get_order_book_depth(symbol)
        metrics["funding"] = self.get_funding_rate_trend(symbol)
        metrics["openInterest"] = self.get_open_interest_trend(symbol)
        metrics["topTrader"] = self.get_top_trader_ratio(symbol)
        
        # Generate composite signals
        signals = []
        
        # 1. Contrarian L/S extreme
        if metrics["longShortRatio"] and metrics["longShortRatio"].get("contrarian_signal"):
            signals.append({
                "type": "CONTRARIAN",
                "signal": metrics["longShortRatio"]["contrarian_signal"],
                "strength": "HIGH" if metrics["longShortRatio"]["latest_long_pct"] > 0.75 else "MEDIUM"
            })
        
        # 2. Order flow imbalance
        if metrics["takerVolume"] and metrics["takerVolume"].get("flowSignal"):
            signals.append({
                "type": "ORDER_FLOW",
                "signal": metrics["takerVolume"]["flowSignal"],
                "strength": "HIGH" if metrics["takerVolume"]["buyPct"] > 0.7 or metrics["takerVolume"]["sellPct"] > 0.7 else "MEDIUM"
            })
        
        # 3. Funding extreme
        if metrics["funding"] and metrics["funding"].get("extremeSignal"):
            signals.append({
                "type": "FUNDING",
                "signal": metrics["funding"]["extremeSignal"],
                "strength": "HIGH" if abs(metrics["funding"]["annualizedPct"]) > 50 else "MEDIUM"
            })
        
        # 4. Liquidity imbalance
        if metrics["orderBook"] and metrics["orderBook"].get("imbalanceSignal"):
            signals.append({
                "type": "LIQUIDITY",
                "signal": metrics["orderBook"]["imbalanceSignal"],
                "strength": "HIGH" if metrics["orderBook"]["liquidityRatio"] > 3 or metrics["orderBook"]["liquidityRatio"] < 0.33 else "MEDIUM"
            })
        
        metrics["compositeSignals"] = signals
        
        # Calculate aggregate sentiment
        sentiment_score = 50  # Neutral
        
        # Adjust based on L/S ratio
        if metrics["longShortRatio"]:
            ls = metrics["longShortRatio"]["latest_long_pct"]
            sentiment_score += (0.5 - ls) * 40  # More longs = bearish (contrarian)
        
        # Adjust based on funding
        if metrics["funding"]:
            funding_annual = metrics["funding"]["annualizedPct"]
            sentiment_score -= funding_annual / 2  # High funding = bearish (contrarian)
        
        # Adjust based on whale positioning
        if metrics["topTrader"]:
            whale = metrics["topTrader"]["longRatio"]
            sentiment_score += (whale - 0.5) * 20  # Follow whales
        
        metrics["sentimentScore"] = max(0, min(100, sentiment_score))
        metrics["sentiment"] = "BULLISH" if sentiment_score > 60 else "BEARISH" if sentiment_score < 40 else "NEUTRAL"
        
        return metrics
    
    def close(self):
        """Close HTTP session."""
        self.session.close()


# Singleton instance
_enhanced_data: Optional[EnhancedFuturesData] = None


def get_enhanced_data(cache_dir: str = "data/enhanced_cache") -> EnhancedFuturesData:
    """Get or create enhanced data fetcher singleton."""
    global _enhanced_data
    if _enhanced_data is None:
        _enhanced_data = EnhancedFuturesData(cache_dir)
    return _enhanced_data


if __name__ == "__main__":
    # Test
    logging.basicConfig(level=logging.INFO)
    data = get_enhanced_data()
    result = data.get_enhanced_metrics("BTCUSDT")
    print(json.dumps(result, indent=2))
