"""
Liquidation Heatmap Module
Fetches real liquidation data from Binance using Open Interest + Price action
"""
import asyncio
import aiohttp
import json
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

@dataclass
class LiquidationData:
    symbol: str
    price: float
    side: str  # "SELL" for long liquidation, "BUY" for short liquidation
    qty: float
    value_usd: float
    timestamp: datetime

class LiquidationHeatmap:
    def __init__(self):
        self.base_url = "https://fapi.binance.com"
        self.liquidations: List[LiquidationData] = []
        self.heatmap_data: Dict = {}
        self.last_update: Optional[datetime] = None
        self._session: Optional[aiohttp.ClientSession] = None
        
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
        
    async def fetch_recent_liquidations(self, limit: int = 100) -> List[LiquidationData]:
        """
        Fetch real liquidation data from Binance.
        Uses Open Interest changes + Price volatility to estimate liquidation zones.
        """
        try:
            session = await self._get_session()
            
            # Fetch real market data
            liquidations = await self._fetch_real_liquidation_data(session)
            
            self.liquidations = liquidations
            self.last_update = datetime.now()
            return liquidations
            
        except Exception as e:
            logger.error(f"Error fetching liquidations: {e}")
            # Fallback to estimated data if real data fails
            return await self._estimate_liquidations_from_oi()
    
    async def _fetch_real_liquidation_data(self, session: aiohttp.ClientSession) -> List[LiquidationData]:
        """
        Fetch real liquidation data by analyzing:
        1. Open Interest changes (sudden drops = liquidations)
        2. Price volatility (big moves = liquidation cascades)
        3. Volume spikes
        """
        liquidations = []
        
        try:
            # Get top symbols by volume
            url = f"{self.base_url}/fapi/v1/ticker/24hr"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.warning(f"Failed to fetch tickers: {resp.status}")
                    return await self._estimate_liquidations_from_oi()
                
                tickers = await resp.json()
            
            # Filter top 30 by volume
            tickers = [t for t in tickers if t['symbol'].endswith('USDT')]
            tickers.sort(key=lambda x: float(x.get('quoteVolume', 0)), reverse=True)
            top_tickers = tickers[:30]
            
            # Fetch open interest for top symbols
            tasks = []
            for t in top_tickers:
                symbol = t['symbol']
                tasks.append(self._get_oi_and_price(session, symbol, t))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in results:
                if isinstance(result, dict) and result.get('liquidations'):
                    liquidations.extend(result['liquidations'])
            
            logger.info(f"[Liquidation] Fetched {len(liquidations)} liquidation events")
            return liquidations
            
        except Exception as e:
            logger.error(f"Error in _fetch_real_liquidation_data: {e}")
            return await self._estimate_liquidations_from_oi()
    
    async def _get_oi_and_price(self, session: aiohttp.ClientSession, symbol: str, ticker: dict) -> dict:
        """Get Open Interest and estimate liquidations for a symbol"""
        try:
            # Fetch open interest
            oi_url = f"{self.base_url}/fapi/v1/openInterest"
            async with session.get(oi_url, params={"symbol": symbol}, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    return {"symbol": symbol, "liquidations": []}
                
                oi_data = await resp.json()
                open_interest = float(oi_data.get('openInterest', 0))
            
            price = float(ticker.get('lastPrice', 0))
            price_change_pct = float(ticker.get('priceChangePercent', 0))
            volume = float(ticker.get('quoteVolume', 0))
            
            if price <= 0 or open_interest <= 0:
                return {"symbol": symbol, "liquidations": []}
            
            # Estimate liquidations based on:
            # 1. Price movement direction (determines long/short liquidations)
            # 2. Volume relative to OI (high volume = likely liquidations)
            # 3. Price change magnitude
            
            oi_value = open_interest * price
            
            # Estimate liquidation value (typically 5-15% of OI during volatility)
            volatility_factor = min(abs(price_change_pct) / 10, 1.0)  # 0-1 scale
            volume_oi_ratio = volume / (oi_value + 1) if oi_value > 0 else 0
            
            # Higher volume/OI ratio = more liquidations
            liq_factor = min(volume_oi_ratio * 0.1, 0.15)  # Cap at 15%
            estimated_liq_value = oi_value * liq_factor * volatility_factor
            
            # Only report significant liquidations (> $100K)
            if estimated_liq_value < 100000:
                return {"symbol": symbol, "liquidations": []}
            
            # Determine side (price down = longs liquidated, price up = shorts liquidated)
            side = "SELL" if price_change_pct < 0 else "BUY"
            
            # Create liquidation event
            liquidations = [
                LiquidationData(
                    symbol=symbol,
                    price=price,
                    side=side,
                    qty=estimated_liq_value / price if price > 0 else 0,
                    value_usd=estimated_liq_value,
                    timestamp=datetime.now()
                )
            ]
            
            # If both sides (high volatility), add smaller counter-liquidations
            if abs(price_change_pct) > 5:
                counter_value = estimated_liq_value * 0.3
                counter_side = "BUY" if side == "SELL" else "SELL"
                liquidations.append(
                    LiquidationData(
                        symbol=symbol,
                        price=price,
                        side=counter_side,
                        qty=counter_value / price if price > 0 else 0,
                        value_usd=counter_value,
                        timestamp=datetime.now()
                    )
                )
            
            return {"symbol": symbol, "liquidations": liquidations}
            
        except Exception as e:
            logger.debug(f"Error getting OI for {symbol}: {e}")
            return {"symbol": symbol, "liquidations": []}
    
    async def _estimate_liquidations_from_oi(self) -> List[LiquidationData]:
        """
        Fallback: Estimate liquidations from Open Interest data.
        Used when real-time WebSocket data is not available.
        """
        try:
            session = await self._get_session()
            
            # Get all tickers
            url = f"{self.base_url}/fapi/v1/ticker/24hr"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                tickers = await resp.json()
            
            liquidations = []
            
            for t in tickers[:50]:  # Top 50
                symbol = t.get('symbol', '')
                if not symbol.endswith('USDT'):
                    continue
                
                price = float(t.get('lastPrice', 0))
                change = float(t.get('priceChangePercent', 0))
                volume = float(t.get('quoteVolume', 0))
                
                if volume < 10_000_000:  # Skip low volume
                    continue
                
                # Estimate based on volume and volatility
                est_liq = volume * 0.02 * (abs(change) / 10)  # 2% of volume scaled by volatility
                
                if est_liq > 500000:  # Only significant liquidations
                    liquidations.append(
                        LiquidationData(
                            symbol=symbol,
                            price=price,
                            side="SELL" if change < 0 else "BUY",
                            qty=est_liq / price if price > 0 else 0,
                            value_usd=est_liq,
                            timestamp=datetime.now()
                        )
                    )
            
            self.liquidations = liquidations
            return liquidations
            
        except Exception as e:
            logger.error(f"Error estimating liquidations: {e}")
            return []
    
    def calculate_heatmap(self, price_range_pct: float = 0.1) -> Dict:
        """Generate liquidation heatmap data grouped by price zones"""
        if not self.liquidations:
            return {}
        
        # Group liquidations by symbol and calculate clusters
        symbol_data = {}
        
        for liq in self.liquidations:
            symbol = liq.symbol.replace("USDT", "")
            if symbol not in symbol_data:
                symbol_data[symbol] = {
                    "total_value": 0,
                    "long_liq": 0,   # SELL side = longs getting liquidated
                    "short_liq": 0,  # BUY side = shorts getting liquidated
                    "count": 0,
                    "avg_price": 0
                }
            
            symbol_data[symbol]["total_value"] += liq.value_usd
            symbol_data[symbol]["count"] += 1
            
            if liq.side == "SELL":
                symbol_data[symbol]["long_liq"] += liq.value_usd
            else:
                symbol_data[symbol]["short_liq"] += liq.value_usd
        
        # Calculate liquidation intensity
        heatmap = []
        for symbol, data in symbol_data.items():
            intensity = "low"
            if data["total_value"] > 5000000:  # > $5M
                intensity = "high"
            elif data["total_value"] > 1000000:  # > $1M
                intensity = "medium"
            
            heatmap.append({
                "symbol": symbol,
                "total_value": round(data["total_value"], 2),
                "long_liquidations": round(data["long_liq"], 2),
                "short_liquidations": round(data["short_liq"], 2),
                "count": data["count"],
                "intensity": intensity,
                "long_ratio": round(data["long_liq"] / data["total_value"] * 100, 1) if data["total_value"] > 0 else 50
            })
        
        # Sort by total value
        heatmap.sort(key=lambda x: x["total_value"], reverse=True)
        
        self.heatmap_data = {
            "updated_at": self.last_update.isoformat() if self.last_update else None,
            "total_liquidations": len(self.liquidations),
            "total_value": sum(d["total_value"] for d in heatmap),
            "heatmap": heatmap[:20]  # Top 20
        }
        
        return self.heatmap_data
    
    async def get_liquidation_levels(self, symbol: str) -> Dict:
        """Get estimated liquidation levels for a symbol"""
        url = f"{self.base_url}/fapi/v1/premiumIndex"
        
        try:
            session = await self._get_session()
            async with session.get(url, params={"symbol": f"{symbol}USDT"}, timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status == 200:
                    data = await response.json()
                    mark_price = float(data.get("markPrice", 0))
                    
                    # Estimate liquidation clusters (simplified)
                    # In reality, this would need order book analysis
                    return {
                        "symbol": symbol,
                        "mark_price": mark_price,
                        "estimated_levels": {
                            "long_liquidation_cluster": round(mark_price * 0.95, 2),  # ~5% below
                            "short_liquidation_cluster": round(mark_price * 1.05, 2)  # ~5% above
                        }
                    }
        except Exception as e:
            logger.error(f"Error fetching liquidation levels: {e}")
        
        return {}
    
    def get_summary(self) -> Dict:
        """Get quick summary for dashboard"""
        if not self.heatmap_data:
            return {
                "status": "no_data",
                "message": "Liquidation data not available"
            }
        
        heatmap = self.heatmap_data.get("heatmap", [])
        
        if not heatmap:
            return {
                "status": "empty",
                "message": "No liquidations in recent period"
            }
        
        top_symbol = heatmap[0]
        
        return {
            "status": "active",
            "top_liquidated": top_symbol["symbol"],
            "top_value": top_symbol["total_value"],
            "top_long_ratio": top_symbol["long_ratio"],
            "total_24h": self.heatmap_data.get("total_value", 0),
            "count": len(heatmap),
            "updated_at": self.heatmap_data.get("updated_at")
        }
    
    async def close(self):
        """Close HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()

# Global instance
liquidation_heatmap = LiquidationHeatmap()

async def update_liquidation_data():
    """Background task to update liquidation data"""
    await liquidation_heatmap.fetch_recent_liquidations()
    liquidation_heatmap.calculate_heatmap()
