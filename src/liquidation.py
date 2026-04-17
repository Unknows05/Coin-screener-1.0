"""
Liquidation Heatmap Module
Fetches liquidation data from Binance
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
        
    async def fetch_recent_liquidations(self, limit: int = 100) -> List[LiquidationData]:
        """Fetch recent liquidation data from Binance"""
        # Try multiple endpoints - some require API key, others don't
        urls_to_try = [
            # Mark price and liquidation data (public)
            (f"{self.base_url}/fapi/v1/markPriceKlines", {"symbol": "BTCUSDT", "interval": "1h", "limit": 1}),
        ]
        
        try:
            async with aiohttp.ClientSession() as session:
                # For now, generate mock liquidation data based on recent price action
                # In production, you'd need API keys for forceOrders endpoint
                liquidations = self._generate_sample_liquidations()
                
                self.liquidations = liquidations
                self.last_update = datetime.now()
                return liquidations
        except Exception as e:
            logger.error(f"Error fetching liquidations: {e}")
            return []
    
    def _generate_sample_liquidations(self) -> List[LiquidationData]:
        """Generate sample liquidation data for demo purposes"""
        # This is a placeholder - in production, use real data from Binance
        sample_data = [
            LiquidationData("BTCUSDT", 74500, "SELL", 1.5, 111750, datetime.now()),
            LiquidationData("ETHUSDT", 2350, "BUY", 10, 23500, datetime.now()),
            LiquidationData("SOLUSDT", 86.5, "SELL", 50, 4325, datetime.now()),
            LiquidationData("1000PEPEUSDT", 0.00388, "SELL", 5000000, 19400, datetime.now()),
            LiquidationData("1000FLOKIUSDT", 0.0309, "SELL", 1500000, 46350, datetime.now()),
        ]
        return sample_data
    
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
