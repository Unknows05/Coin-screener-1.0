"""
Screening Engine V2 — Enhanced dengan Microstructure Data.

Integrasi:
- EnhancedDataV2: Real liquidations, whale flow, order book
- RegimeDetectorV2: Microstructure-based regime detection
- RiskManagerV2: Liquidation cascade blocking, whale divergence

Optimizations:
- Parallel fetching untuk efficiency
- Smart caching untuk reduce API calls
- Configurable feature flags
"""
import time
import json
import logging
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from src.binance_api import BinanceFuturesAPI
from src.scorer import Scorer
from src.signals import generate_signal
from src.database import ScreenerDB
from src.alerter import SignalAlerter
from src.regime import RegimeDetector
from src.utils import is_leveraged_token
from src.adaptive_rl import get_optimizer, AdaptiveSignalOptimizer

# V1 imports (fallback)
from src.enhanced_data import get_enhanced_data, EnhancedFuturesData
from src.risk_manager import get_risk_manager, RiskManager, RiskConfig

# V2 imports (new microstructure features)
from src.enhanced_data_v2 import EnhancedDataV2, get_enhanced_v2
from src.regime_v2 import RegimeDetectorV2, get_regime_v2
from src.risk_manager_v2 import RiskManagerV2, get_risk_manager_v2

logger = logging.getLogger(__name__)


class ScreeningEngineV2:
    """
    Enhanced screening engine dengan microstructure intelligence.
    
    Improvements over V1:
    1. Real liquidation data (forceOrders endpoint)
    2. Whale position tracking (size-weighted, not account-weighted)
    3. Early regime flip detection (minutes not hours)
    4. Liquidation cascade circuit breaker
    5. Whale divergence warnings
    6. Order book wall detection
    
    All features tunable via config.
    """

    def __init__(self, config: dict, cache_dir: str = "data"):
        """
        Initialize enhanced screening engine.
        
        Args:
            config: Standard config dict
            cache_dir: Data directory
        """
        self.config = config
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "last_scan.json"
        
        # Feature flags from config
        micro_config = config.get("microstructure", {})
        self.use_microstructure = micro_config.get("enabled", True)
        
        # Enhanced symbols config (null = all)
        enhanced_symbols = micro_config.get("enhanced_symbols")
        
        # Internal state
        self._lock = threading.Lock()
        self._last_result: list[dict] = []
        self._is_scanning: bool = False
        self._scan_count: int = 0
        self._last_scan_time: Optional[str] = None
        self._next_scan_time: Optional[str] = None
        self._last_elapsed: float = 0
        self._last_error: Optional[str] = None

        # Lazy init
        self._api: Optional[BinanceFuturesAPI] = None
        self._scorer: Optional[Scorer] = None
        self._regime_detector: Optional[RegimeDetector] = None
        self._regime_detector_v2: Optional[RegimeDetectorV2] = None

        # Config values
        self.symbols = config.get("symbols", [])
        self.auto_discover = config.get("scan", {}).get("auto_discover", False)
        self.min_quote_volume = config.get("scan", {}).get("min_quote_volume", 5_000_000)
        self.timeframes = config.get("timeframes", ["15m", "1h", "4h"])
        self.kline_limit = config.get("scan", {}).get("kline_limit", 200)
        
        # Enhanced data config
        # Default: all symbols get enhanced data (not just top 10)
        self.enhanced_symbols = set(enhanced_symbols or self.symbols)

        # Discovered symbols (auto-discover from Binance)
        self._discovered_symbols: list[str] = []

        # Core components
        self.db = ScreenerDB(str(self.cache_dir / "screener.db"))
        self.alerter = SignalAlerter()
        self.rl_optimizer: AdaptiveSignalOptimizer = get_optimizer(str(self.cache_dir / "screener.db"))
        
        # V1 components (fallback)
        self.enhanced_data_v1: EnhancedFuturesData = get_enhanced_data(str(self.cache_dir / "enhanced_cache"))
        self.risk_manager_v1: RiskManager = get_risk_manager(RiskConfig(), str(self.cache_dir / "screener.db"))
        
        # V2 components (microstructure)
        if self.use_microstructure:
            micro_config = config.get("microstructure", {})
            self._enhanced_data_v2: EnhancedDataV2 = get_enhanced_v2(
                api=self.api,
                config=micro_config
            )
            self._regime_detector_v2: RegimeDetectorV2 = get_regime_v2(self._enhanced_data_v2)
            self._risk_manager_v2: RiskManagerV2 = get_risk_manager_v2(
                RiskConfig(), 
                str(self.cache_dir / "screener.db"),
                self._enhanced_data_v2,
                micro_config
            )
            logger.info("[EngineV2] Microstructure features ENABLED (all 30 coins)")
        else:
            self._enhanced_data_v2 = None
            self._regime_detector_v2 = None
            self._risk_manager_v2 = None
            logger.info("[EngineV2] Microstructure features DISABLED (V1 mode)")

        # Bounded deque to prevent memory leak - keeps last 100 alerts
        self._last_alerts: deque[dict] = deque(maxlen=100)
        
        # Statistics
        self._blocked_by_liquidation: int = 0
        self._blocked_by_divergence: int = 0
        self._microstructure_enhanced: int = 0

        logger.info(
            f"[EngineV2] Initialized: {len(self.symbols)} symbols, "
            f"enhanced_symbols={len(self.enhanced_symbols)}, "
            f"microstructure={self.use_microstructure}"
        )

    # ---- Lazy initialization ----

    @property
    def api(self) -> BinanceFuturesAPI:
        if self._api is None:
            self._api = BinanceFuturesAPI()
        return self._api

    @property
    def scorer(self) -> Scorer:
        if self._scorer is None:
            self._scorer = Scorer(self.config)
        return self._scorer

    @property
    def regime_detector(self) -> RegimeDetector:
        if self._regime_detector is None:
            regime_config = self.config.get("regime", {})
            self._regime_detector = RegimeDetector(
                adx_threshold=regime_config.get("adx_threshold", 25),
                vol_lookback=regime_config.get("vol_lookback", 20),
                atr_period=regime_config.get("atr_period", 14)
            )
        return self._regime_detector

    @property
    def regime_detector_v2(self) -> RegimeDetectorV2:
        if self._regime_detector_v2 is None and self.use_microstructure:
            self._regime_detector_v2 = get_regime_v2(self._enhanced_data_v2)
        return self._regime_detector_v2

    # ---- Auto-discover coins ----

    def discover_coins(self) -> list[str]:
        """Discover all USDT-M perpetual coins with minimum volume."""
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                future_exchange = executor.submit(self.api.get_exchange_info)
                future_tickers = executor.submit(self.api.get_all_tickers)
                exchange_info = future_exchange.result()
                tickers = future_tickers.result()

            ticker_vol = {t["symbol"]: float(t.get("quoteVolume", 0)) for t in tickers}

            coins = []
            for s in exchange_info.get("symbols", []):
                if (s.get("contractType") == "PERPETUAL"
                        and s.get("status") == "TRADING"
                        and s.get("quoteAsset") == "USDT"):
                    sym = s["symbol"]
                    if is_leveraged_token(sym):
                        continue
                    vol = ticker_vol.get(sym, 0)
                    if vol >= self.min_quote_volume:
                        coins.append(sym)

            coins.sort()
            self._discovered_symbols = coins
            logger.info(f"[EngineV2] Discovered {len(coins)} coins (min vol ${self.min_quote_volume:,.0f})")
            return coins
        except Exception as e:
            logger.warning(f"[EngineV2] Auto-discover failed: {e}. Using config symbols.")
            return self.symbols

    def get_active_symbols(self) -> list[str]:
        """Get symbols to scan — discovered or config."""
        if self.auto_discover and not self._discovered_symbols:
            return self.discover_coins()
        if self.auto_discover:
            return self._discovered_symbols
        return self.symbols

    # ---- Core screening V2 ----

    def scan(self) -> dict:
        """
        Run a full screening cycle dengan microstructure enhancement.
        
        Returns:
            dict with: ok, timestamp, elapsed, data, summary, scan_count, microstructure_stats
        """
        with self._lock:
            if self._is_scanning:
                return {
                    "ok": False,
                    "error": "Scan already in progress",
                    "scanning": True
                }
            self._is_scanning = True
            self._last_error = None

        start_time = time.time()
        symbols = self.get_active_symbols()
        
        # Reset per-scan statistics
        self._blocked_by_liquidation = 0
        self._blocked_by_divergence = 0
        self._microstructure_enhanced = 0
        
        logger.info(f"[EngineV2] Starting scan of {len(symbols)} coins (microstructure={self.use_microstructure})...")

        try:
            # Fetch ticker data
            all_tickers = self.api.get_all_tickers()
            ticker_map = {t["symbol"]: t for t in all_tickers}

            results = []
            errors = 0
            microstructure_applied = 0
            blocked_signals = 0

            # Helper to fetch klines for one symbol
            def fetch_klines_for_symbol(symbol: str) -> tuple[str, dict, Exception | None]:
                klines_by_tf = {}
                try:
                    for tf in self.timeframes:
                        klines_by_tf[tf] = self.api.get_klines(symbol, tf, self.kline_limit)
                    return symbol, klines_by_tf, None
                except Exception as e:
                    return symbol, klines_by_tf, e

            # Fetch klines concurrently
            with ThreadPoolExecutor(max_workers=10) as executor:
                future_to_symbol = {
                    executor.submit(fetch_klines_for_symbol, sym): sym
                    for sym in symbols
                }
                
                for future in as_completed(future_to_symbol):
                    symbol, klines_by_tf, error = future.result()
                    if error:
                        logger.warning(f"[EngineV2] Error fetching klines for {symbol}: {error}")
                        errors += 1
                        continue

                    if not any(klines_by_tf.values()):
                        errors += 1
                        continue

                    # Get current price
                    price = float(ticker_map.get(symbol, {}).get("lastPrice", 0))
                    if price == 0 and klines_by_tf.get("15m"):
                        price = klines_by_tf["15m"][-1]["close"]

                    # Detect regime (V1 or V2)
                    regime_data = {"regime": "SIDEWAYS"}
                    micro_data = None
                    flip_detection = None
                    
                    if klines_by_tf.get("15m"):
                        try:
                            regime_df = pd.DataFrame(klines_by_tf["15m"])
                            if len(regime_df) >= 50:
                                # Use V2 if enabled and symbol is in enhanced list
                                if self.use_microstructure and symbol in self.enhanced_symbols:
                                    regime_v2_result = self._regime_detector_v2.detect(
                                        regime_df, symbol, price
                                    )
                                    regime_data = {
                                        "regime": regime_v2_result.regime,
                                        "confidence": regime_v2_result.confidence,
                                        "strength": regime_v2_result.strength,
                                        "microstructure_signals": regime_v2_result.signals,
                                        "evidence": regime_v2_result.evidence,
                                        "trade_direction": regime_v2_result.trade_direction,
                                        "urgency": regime_v2_result.urgency
                                    }
                                    microstructure_applied += 1
                                    
                                    # Also check for market flip
                                    flip_detection = self._regime_detector_v2.detect_market_flip(
                                        symbol, price
                                    )
                                    
                                    # Get full microstructure untuk risk manager
                                    micro_data = self._enhanced_data_v2.get_full_microstructure(
                                        symbol, price
                                    )
                                else:
                                    # Fallback to V1
                                    regime_data = self.regime_detector.detect(regime_df)
                        except Exception as e:
                            logger.warning(f"[EngineV2] Regime detection failed for {symbol}: {e}")
                    
                    # Get adaptive RL weights
                    regime_type = regime_data.get("regime", "SIDEWAYS") if isinstance(regime_data, dict) else str(regime_data)
                    adaptive_params = {}
                    try:
                        adaptive_params = self.rl_optimizer.get_recommended_params(regime_type)
                    except Exception:
                        pass

                    # Score with enhanced metrics if available
                    score_result = self.scorer.score_coin(
                        klines_by_tf, regime_type, adaptive_params, 
                        micro_data if self.use_microstructure else None
                    )

                    # Generate signal
                    coin_data = {
                        "symbol": symbol,
                        "price": price,
                        "klines": klines_by_tf.get("15m", []),
                        "regime": regime_data,
                        "flip_detection": flip_detection,
                        **score_result
                    }
                    signal_result = generate_signal(coin_data, self.config)
                    
                    # Add microstructure metadata
                    if self.use_microstructure and micro_data:
                        signal_result["microstructure"] = {
                            "sentiment": micro_data.get("sentiment"),
                            "sentiment_confidence": micro_data.get("confidence"),
                            "signals": micro_data.get("signals", []),
                            "whale_position": micro_data.get("whale_position", {}).get("long_ratio"),
                            "liquidation_pressure": micro_data.get("liquidations", {}).get("pressure"),
                        }
                    
                    # Risk Management Check (V2 jika enabled)
                    if signal_result.get("signal") in ("LONG", "SHORT"):
                        risk_check = None
                        
                        if self.use_microstructure and self._risk_manager_v2:
                            # Use V2 with microstructure
                            market_data = {"microstructure": micro_data} if micro_data else {}
                            risk_check = self._risk_manager_v2.can_trade(
                                {
                                    "symbol": symbol,
                                    "signal": signal_result["signal"],
                                    "confidence": signal_result.get("confidence", 50),
                                    "regime": regime_type,
                                    "price": price,
                                    "sl": signal_result.get("sl"),
                                    "tp": signal_result.get("tp")
                                },
                                market_data=market_data
                            )
                            
                            # Track blocking reasons
                            if not risk_check.get("allowed", True):
                                reason = risk_check.get("reason", "")
                                if "LIQUIDATION" in reason or "CASCADE" in reason:
                                    self._blocked_by_liquidation += 1
                                elif "DIVERGENCE" in reason:
                                    self._blocked_by_divergence += 1
                                    
                                blocked_signals += 1
                        else:
                            # Fallback to V1
                            risk_check = self.risk_manager_v1.can_trade(
                                {
                                    "symbol": symbol,
                                    "signal": signal_result["signal"],
                                    "confidence": signal_result.get("confidence", 50),
                                    "regime": regime_type,
                                    "price": price,
                                    "sl": signal_result.get("sl"),
                                    "tp": signal_result.get("tp")
                                },
                                market_data={}
                            )
                        
                        # Add risk info to signal
                        signal_result["risk_check"] = risk_check
                        signal_result["risk_score"] = risk_check.get("risk_score", 0)
                        
                        # Log warnings
                        if risk_check.get("warnings"):
                            for warning in risk_check["warnings"]:
                                if warning:
                                    logger.warning(f"[EngineV2] [Risk] {symbol}: {warning}")
                        
                        # If trading not allowed, downgrade to WAIT
                        if not risk_check.get("allowed", True):
                            logger.warning(
                                f"[EngineV2] [Risk] {symbol}: Trade blocked - {risk_check.get('reason', 'Unknown')}"
                            )
                            signal_result["signal"] = "WAIT"
                            signal_result["risk_blocked"] = True
                            signal_result["risk_reason"] = risk_check.get("reason", "Risk check failed")
                    
                    results.append(signal_result)

            elapsed = time.time() - start_time

            # Build summary
            long_count = sum(1 for r in results if r["signal"] == "LONG")
            short_count = sum(1 for r in results if r["signal"] == "SHORT")
            wait_count = sum(1 for r in results if r["signal"] == "WAIT")
            blocked_count = sum(1 for r in results if r.get("risk_blocked"))

            scan_result = {
                "ok": True,
                "timestamp": datetime.now().isoformat(),
                "elapsed_seconds": round(elapsed, 1),
                "data": results,
                "summary": {
                    "total": len(results),
                    "long": long_count,
                    "short": short_count,
                    "wait": wait_count,
                    "blocked_by_risk": blocked_count,
                    "errors": errors
                },
                "microstructure_stats": {
                    "enabled": self.use_microstructure,
                    "coins_analyzed": microstructure_applied,
                    "blocked_by_liquidation": self._blocked_by_liquidation,
                    "blocked_by_divergence": self._blocked_by_divergence,
                    "total_blocked": blocked_signals
                } if self.use_microstructure else None
            }

            # Update state
            with self._lock:
                self._last_result = results
                self._scan_count += 1
                self._last_scan_time = scan_result["timestamp"]
                self._last_elapsed = elapsed
                self._is_scanning = False

            # Cache to file
            self._save_cache(scan_result)

            # Stage 2: Database Tracking
            ts = scan_result["timestamp"]
            try:
                # 1. Save new signals to DB
                self.db.save_signals(ts, results)

                # 2. Check outcomes
                price_map = {t["symbol"]: float(t.get("lastPrice", 0)) for t in all_tickers}
                self.db.check_outcomes(price_map)

                # 3. Alerts
                alerts = self.alerter.check(results, ts)
                self._last_alerts = alerts
                if alerts:
                    logger.info(f"[EngineV2] {len(alerts)} signal alerts detected")
                
                # 4. RL: Update adaptive weights
                try:
                    rl_updates = self.rl_optimizer.update_weights()
                    if rl_updates:
                        logger.info(f"[EngineV2] RL: Updated weights for {len(rl_updates)} regimes")
                except Exception as rl_err:
                    logger.debug(f"[EngineV2] RL update skipped: {rl_err}")
                    
            except Exception as e:
                logger.warning(f"[EngineV2] DB/Alert/RL error: {e}")

            logger.info(
                f"[EngineV2] Scan complete: {len(results)} coins, "
                f"{long_count}L/{short_count}S/{wait_count}W in {elapsed:.1f}s, "
                f"blocked={blocked_count}"
            )
            
            if self.use_microstructure:
                logger.info(
                    f"[EngineV2] Microstructure: {microstructure_applied} coins analyzed, "
                    f"liq_blocks={self._blocked_by_liquidation}, "
                    f"div_blocks={self._blocked_by_divergence}"
                )

            return scan_result

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[EngineV2] Scan failed: {error_msg}")
            with self._lock:
                self._is_scanning = False
                self._last_error = error_msg
            return {
                "ok": False,
                "error": error_msg,
                "timestamp": datetime.now().isoformat()
            }

    # ---- Read cached results ----

    def get_latest_scan(self) -> Optional[dict]:
        """Get the latest scan result from memory."""
        with self._lock:
            if not self._last_result:
                pass  # fall through to _load_cache

        if not self._last_result:
            cached = self._load_cache()
            if cached:
                return cached

        return self._build_response()

    def _build_response(self) -> Optional[dict]:
        """Build response dari _last_result."""
        with self._lock:
            if not self._last_result:
                return None

            # Calculate microstructure stats from last scan results
            micro_stats = None
            if self.use_microstructure:
                blocked_liq = sum(1 for r in self._last_result if r.get("risk_blocked") and "LIQUIDATION" in str(r.get("risk_reason", "")))
                blocked_div = sum(1 for r in self._last_result if r.get("risk_blocked") and "DIVERGENCE" in str(r.get("risk_reason", "")))
                total_blocked = sum(1 for r in self._last_result if r.get("risk_blocked"))
                micro_stats = {
                    "enabled": True,
                    "coins_analyzed": sum(1 for r in self._last_result if r.get("microstructure")),
                    "blocked_by_liquidation": blocked_liq,
                    "blocked_by_divergence": blocked_div,
                    "total_blocked": total_blocked
                }

            return {
                "ok": True,
                "timestamp": self._last_scan_time or datetime.now().isoformat(),
                "elapsed_seconds": round(self._last_elapsed, 1),
                "data": self._last_result,
                "summary": self._calculate_summary(self._last_result),
                "microstructure_stats": micro_stats
            }

    def _calculate_summary(self, results: list) -> dict:
        """Calculate summary statistics."""
        longs = sum(1 for r in results if r["signal"] == "LONG")
        shorts = sum(1 for r in results if r["signal"] == "SHORT")
        waits = sum(1 for r in results if r["signal"] == "WAIT")
        blocked = sum(1 for r in results if r.get("risk_blocked"))
        
        return {
            "total": len(results),
            "long": longs,
            "short": shorts,
            "wait": waits,
            "blocked_by_risk": blocked
        }

    def get_signals(self) -> dict:
        """Get only coins with LONG or SHORT signals."""
        result = self.get_latest_scan()
        if result is None or not result.get("ok"):
            return {"ok": False, "error": "No scan data available"}

        signals = [r for r in result["data"] if r["signal"] in ("LONG", "SHORT")]
        result["data"] = signals
        result["summary"]["active_signals"] = len(signals)
        return result

    def get_status(self) -> dict:
        """Get system status."""
        with self._lock:
            active = self._discovered_symbols if self.auto_discover else self.symbols
            return {
                "ok": True,
                "running": True,
                "is_scanning": self._is_scanning,
                "last_scan": self._last_scan_time,
                "next_scan": self._next_scan_time,
                "total_scans": self._scan_count,
                "last_elapsed_seconds": round(self._last_elapsed, 1),
                "total_coins": len(active),
                "discovered_coins": len(self._discovered_symbols),
                "auto_discover": self.auto_discover,
                "timeframes": self.timeframes,
                "mode": self.config.get("signal", {}).get("mode", "normal"),
                "microstructure_enabled": self.use_microstructure,
                "last_error": self._last_error
            }

    def get_signals_history(self, limit: int = 100) -> list[dict]:
        """Get signal history from database with outcomes."""
        return self.db.get_signals_with_outcomes(limit)

    def get_calendar(self, year: int, month: int) -> list[dict]:
        """Get calendar view for a month."""
        return self.db.get_calendar_month(year, month)

    def get_db_stats(self) -> dict:
        """Get database statistics."""
        return self.db.get_summary()

    # ---- Cache management ----

    def _save_cache(self, data: dict):
        """Save scan result to file for persistence."""
        try:
            compact_data = []
            for item in data["data"]:
                compact_item = {
                    "symbol": item.get("symbol", ""),
                    "price": item.get("price", 0),
                    "signal": item.get("signal", "WAIT"),
                    "confidence": item.get("confidence", 0),
                    "entry": item.get("entry"),
                    "sl": item.get("sl"),
                    "tp": item.get("tp"),
                    "regime": item.get("regime", "SIDEWAYS"),
                    "score": item.get("score", 0),
                    "reasons": item.get("reasons", []),
                    "patterns_detected": item.get("patterns_detected", []),
                    "risk_blocked": item.get("risk_blocked", False),
                    "risk_score": item.get("risk_score", 0)
                }
                compact_data.append(compact_item)

            cache_data = {
                "timestamp": data["timestamp"],
                "elapsed": data["elapsed_seconds"],
                "data": compact_data,
                "summary": data["summary"],
                "microstructure_stats": data.get("microstructure_stats")
            }
            with open(self.cache_file, "w") as f:
                json.dump(cache_data, f)
        except Exception as e:
            logger.warning(f"[EngineV2] Cache save failed: {e}")

    def _load_cache(self) -> Optional[dict]:
        """Load from cache file."""
        try:
            with open(self.cache_file, "r") as f:
                cached = json.load(f)
            
            with self._lock:
                self._last_result = cached.get("data", [])
                self._last_scan_time = cached.get("timestamp")
                self._last_elapsed = cached.get("elapsed", 0)
            
            return {
                "ok": True,
                "timestamp": cached.get("timestamp"),
                "elapsed_seconds": cached.get("elapsed", 0),
                "data": cached.get("data", []),
                "summary": cached.get("summary", {}),
                "microstructure_stats": cached.get("microstructure_stats")
            }
        except Exception as e:
            logger.debug(f"[EngineV2] Cache load failed: {e}")
            return None

    def set_next_scan(self, timestamp: str):
        """Set the next scheduled scan time."""
        with self._lock:
            self._next_scan_time = timestamp

    def close(self):
        """Cleanup resources."""
        self.db.close()
        if self._enhanced_data_v2:
            self._enhanced_data_v2.clear_cache()
        logger.info("[EngineV2] Closed")


# Backward compatibility alias
ScreeningEngine = ScreeningEngineV2
