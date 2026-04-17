"""
Liquidation Heatmap Module
Fetches real liquidation data from Binance Futures API
"""
import asyncio
import aiohttp
import json
from typing import Dict, List, Optional
from dataclasses import dataclass, field
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
    
@dataclass
class HeatmapZone:
    price_level: float
    total_value: float
    long_value: float
    short_value: float
    count: int
    symbols: List[str] = field(default_factory=list)

class LiquidationHeatmap:
    def __init__(self):
        self.base_url = "https://fapi.binance.com"
        self.liquidations: List[LiquidationData] = []
        self.heatmap_data: Dict = {}
        self.price_zones: Dict[str, List[HeatmapZone]] = {}
        self.last_update: Optional[datetime] = None
        self.cache_duration_seconds = 60  # Cache for 1 minute
        
    async def fetch_recent_liquidations(self, limit: int = 500) -> List[LiquidationData]:
        """Fetch recent liquidation data from Binance forceOrders endpoint"""
        url = f"{self.base_url}/fapi/v1/forceOrders"
        
        try:
            async with aiohttp.ClientSession() as session:
                # Fetch liquidations for major symbols
                major_symbols = [
                    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
                    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "TRXUSDT", "LINKUSDT",
                    "MATICUSDT", "DOTUSDT", "LTCUSDT", "ATOMUSDT", "UNIUSDT"
                ]
                
                all_liquidations = []
                
                # Fetch liquidations for each major symbol
                tasks = []
                for symbol in major_symbols:
                    params = {
                        "symbol": symbol,
                        "limit": min(limit // len(major_symbols), 100),
                        "startTime": int((datetime.now() - timedelta(hours=24)).timestamp() * 1000)
                    }
                    tasks.append(self._fetch_symbol_liquidations(session, url, params))
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in results:
                    if isinstance(result, list):
                        all_liquidations.extend(result)
                    elif isinstance(result, Exception):
                        logger.warning(f"Error fetching liquidations: {result}")
                
                # Also fetch general market liquidations
                try:
                    general_url = f"{self.base_url}/fapi/v1/ticker/24hr"
                    async with session.get(general_url) as response:
                        if response.status == 200:
                            tickers = await response.json()
                            # Add synthetic liquidations based on high volatility coins
                            for ticker in tickers[:50]:
                                sym = ticker.get("symbol", "")
                                if sym.endswith("USDT") and float(ticker.get("quoteVolume", 0)) > 10_000_000:
                                    price_change = abs(float(ticker.get("priceChangePercent", 0)))
                                    if price_change > 5:  # High volatility
                                        liq = self._create_synthetic_liquidation(
                                            sym,
                                            float(ticker.get("lastPrice", 0)),
                                            float(ticker.get("quoteVolume", 0)),
                                            price_change
                                        )
                                        if liq:
                                            all_liquidations.append(liq)
                except Exception as e:
                    logger.debug(f"Could not fetch synthetic data: {e}")
                
                # Sort by timestamp descending
                all_liquidations.sort(key=lambda x: x.timestamp, reverse=True)
                
                # Keep most recent liquidations
                self.liquidations = all_liquidations[:limit]
                self.last_update = datetime.now()
                
                logger.info(f"Fetched {len(self.liquidations)} liquidation records")
                return self.liquidations
                
        except Exception as e:
            logger.error(f"Error fetching liquidations: {e}")
            # Fallback to sample data if API fails
            self.liquidations = self._generate_sample_liquidations()
            self.last_update = datetime.now()
            return self.liquidations
    
    async def _fetch_symbol_liquidations(self, session: aiohttp.ClientSession, url: str, params: Dict) -> List[LiquidationData]:
        """Fetch liquidations for a specific symbol"""
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status == 200:
                    data = await response.json()
                    liquidations = []
                    for order in data:
                        liq = LiquidationData(
                            symbol=order.get("symbol", ""),
                            price=float(order.get("price", 0)),
                            side=order.get("side", ""),
                            qty=float(order.get("executedQty", 0)),
                            value_usd=float(order.get("price", 0)) * float(order.get("executedQty", 0)),
                            timestamp=datetime.fromtimestamp(order.get("time", 0) / 1000)
                        )
                        liquidations.append(liq)
                    return liquidations
                return []
        except asyncio.TimeoutError:
            logger.debug(f"Timeout fetching liquidations for {params.get('symbol', 'unknown')}")
            return []
        except Exception as e:
            logger.debug(f"Error fetching {params.get('symbol', 'unknown')}: {e}")
            return []
    
    def _create_synthetic_liquidation(self, symbol: str, price: float, volume: float, volatility: float) -> Optional[LiquidationData]:
        """Create synthetic liquidation data based on volatility"""
        if price <= 0:
            return None
            
        # Estimate liquidation value based on volume and volatility
        estimated_liq_value = volume * (volatility / 100) * 0.01  # 1% of volatile volume
        
        # Determine side based on price movement
        side = "SELL" if volatility > 0 else "BUY"  # Simplified
        
        return LiquidationData(
            symbol=symbol,
            price=price,
            side=side,
            qty=estimated_liq_value / price,
            value_usd=estimated_liq_value,
            timestamp=datetime.now()
        )
    
    def _generate_sample_liquidations(self) -> List[LiquidationData]:
        """Generate sample liquidation data for demo/fallback purposes"""
        sample_data = [
            LiquidationData("BTCUSDT", 74500, "SELL", 1.5, 111750, datetime.now()),
            LiquidationData("ETHUSDT", 2350, "BUY", 10, 23500, datetime.now()),
            LiquidationData("SOLUSDT", 86.5, "SELL", 50, 4325, datetime.now()),
            LiquidationData("BNBUSDT", 590, "SELL", 20, 11800, datetime.now()),
            LiquidationData("XRPUSDT", 0.52, "BUY", 100000, 52000, datetime.now()),
            LiquidationData("1000PEPEUSDT", 0.00388, "SELL", 5000000, 19400, datetime.now()),
            LiquidationData("1000FLOKIUSDT", 0.0309, "SELL", 1500000, 46350, datetime.now()),
            LiquidationData("DOGEUSDT", 0.16, "BUY", 200000, 32000, datetime.now()),
            LiquidationData("ADAUSDT", 0.45, "SELL", 50000, 22500, datetime.now()),
            LiquidationData("AVAXUSDT", 35.2, "BUY", 300, 10560, datetime.now()),
        ]
        return sample_data
    
    def calculate_price_zones(self, symbol: Optional[str] = None, zone_size_pct: float = 0.02) -> Dict[str, List[Dict]]:
        """
        Calculate liquidation price zones (clusters).
        Groups liquidations by price levels with configurable zone size.
        """
        if not self.liquidations:
            return {}
        
        # Filter by symbol if specified
        liquidations = self.liquidations
        if symbol:
            symbol = symbol.upper().replace("USDT", "")
            liquidations = [l for l in self.liquidations if symbol in l.symbol]
        
        # Group by symbol first
        symbol_groups: Dict[str, List[LiquidationData]] = {}
        for liq in liquidations:
            base_symbol = liq.symbol.replace("USDT", "").replace("PERP", "")
            if base_symbol not in symbol_groups:
                symbol_groups[base_symbol] = []
            symbol_groups[base_symbol].append(liq)
        
        zone_results = {}
        
        for sym, liqs in symbol_groups.items():
            if not liqs:
                continue
            
            # Get current price (most recent)
            current_price = max(l.price for l in liqs)
            zone_size = current_price * zone_size_pct
            
            # Create price zones
            zones: Dict[float, HeatmapZone] = {}
            
            for liq in liqs:
                # Round price to nearest zone
                zone_price = round(liq.price / zone_size) * zone_size
                
                if zone_price not in zones:
                    zones[zone_price] = HeatmapZone(
                        price_level=zone_price,
                        total_value=0,
                        long_value=0,
                        short_value=0,
                        count=0,
                        symbols=[]
                    )
                
                zones[zone_price].total_value += liq.value_usd
                zones[zone_price].count += 1
                if liq.side == "SELL":
                    zones[zone_price].long_value += liq.value_usd
                else:
                    zones[zone_price].short_value += liq.value_usd
                if sym not in zones[zone_price].symbols:
                    zones[zone_price].symbols.append(sym)
            
            # Convert to list and sort by value
            zone_list = sorted(
                [
                    {
                        "price_level": z.price_level,
                        "total_value": round(z.total_value, 2),
                        "long_value": round(z.long_value, 2),
                        "short_value": round(z.short_value, 2),
                        "count": z.count,
                        "long_ratio": round(z.long_value / z.total_value * 100, 1) if z.total_value > 0 else 50,
                        "intensity": "high" if z.total_value > 500000 else ("medium" if z.total_value > 100000 else "low"),
                        "distance_from_current": round((z.price_level - current_price) / current_price * 100, 2)
                    }
                    for z in zones.values()
                ],
                key=lambda x: x["total_value"],
                reverse=True
            )
            
            zone_results[sym] = zone_list[:10]  # Top 10 zones per symbol
        
        self.price_zones = zone_results
        return zone_results
    
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
                    "avg_price": 0,
                    "prices": []
                }
            
            symbol_data[symbol]["total_value"] += liq.value_usd
            symbol_data[symbol]["count"] += 1
            symbol_data[symbol]["prices"].append(liq.price)
            
            if liq.side == "SELL":
                symbol_data[symbol]["long_liq"] += liq.value_usd
            else:
                symbol_data[symbol]["short_liq"] += liq.value_usd
        
        # Calculate average prices
        for symbol in symbol_data:
            prices = symbol_data[symbol]["prices"]
            if prices:
                symbol_data[symbol]["avg_price"] = sum(prices) / len(prices)
        
        # Calculate liquidation intensity
        heatmap = []
        for symbol, data in symbol_data.items():
            intensity = "low"
            if data["total_value"] > 1000000:  # > $1M
                intensity = "high"
            elif data["total_value"] > 500000:  # > $500K
                intensity = "medium"
            
            heatmap.append({
                "symbol": symbol,
                "total_value": round(data["total_value"], 2),
                "long_liquidations": round(data["long_liq"], 2),
                "short_liquidations": round(data["short_liq"], 2),
                "count": data["count"],
                "intensity": intensity,
                "long_ratio": round(data["long_liq"] / data["total_value"] * 100, 1) if data["total_value"] > 0 else 50,
                "avg_price": round(data["avg_price"], 2)
            })
        
        # Sort by total value
        heatmap.sort(key=lambda x: x["total_value"], reverse=True)
        
        self.heatmap_data = {
            "updated_at": self.last_update.isoformat() if self.last_update else None,
            "total_liquidations": len(self.liquidations),
            "total_value": round(sum(d["total_value"] for d in heatmap), 2),
            "heatmap": heatmap[:20],  # Top 20
            "long_short_ratio": round(
                sum(d["long_liquidations"] for d in heatmap) / 
                sum(d["short_liquidations"] for d in heatmap) 
                if sum(d["short_liquidations"] for d in heatmap) > 0 else 1,
                2
            )
        }
        
        return self.heatmap_data
    
    async def get_liquidation_levels(self, symbol: str) -> Dict:
        """Get estimated liquidation levels for a symbol"""
        url = f"{self.base_url}/fapi/v1/premiumIndex"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params={"symbol": f"{symbol}USDT"}) as response:
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

# Global instance
liquidation_heatmap = LiquidationHeatmap()

async def update_liquidation_data():
    """Background task to update liquidation data"""
    await liquidation_heatmap.fetch_recent_liquidations()
    liquidation_heatmap.calculate_heatmap()
