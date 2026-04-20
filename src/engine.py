"""
Screening Engine — Orchestrator for coin screening.
Thread-safe, reusable, singleton-pattern.
Wraps all src/* modules into a single interface.
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
from src.enhanced_data import get_enhanced_data, EnhancedFuturesData

logger = logging.getLogger(__name__)


class ScreeningEngine:
    """
    Core screening engine — orchestrates data fetching, scoring, and signal generation.

    Thread-safe: uses locks for shared state access.
    Singleton: one instance per application (enforced by caller).
    """

    def __init__(self, config: dict, cache_dir: str = "data"):
        self.config = config
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "last_scan.json"

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

        # Config values
        self.symbols = config.get("symbols", [])
        self.auto_discover = config.get("scan", {}).get("auto_discover", False)
        self.min_quote_volume = config.get("scan", {}).get("min_quote_volume", 5_000_000)
        self.timeframes = config.get("timeframes", ["15m", "1h", "4h"])
        self.kline_limit = config.get("scan", {}).get("kline_limit", 200)

        # Discovered symbols (auto-discover from Binance)
        self._discovered_symbols: list[str] = []

        # Database + Alerter + RL Optimizer + Enhanced Data
        self.db = ScreenerDB(str(self.cache_dir / "screener.db"))
        self.alerter = SignalAlerter()
        self.rl_optimizer: AdaptiveSignalOptimizer = get_optimizer(str(self.cache_dir / "screener.db"))
        self.enhanced_data: EnhancedFuturesData = get_enhanced_data(str(self.cache_dir / "enhanced_cache"))
        # Bounded deque to prevent memory leak - keeps last 100 alerts
        self._last_alerts: deque[dict] = deque(maxlen=100)

        logger.info(
            f"[Engine] Initialized: {len(self.symbols)} config symbols, "
            f"auto_discover={self.auto_discover}, timeframes: {self.timeframes}, RL=enabled"
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

    # ---- Auto-discover coins ----

    def discover_coins(self) -> list[str]:
        """Discover all USDT-M perpetual coins with minimum volume."""
        try:
            # Fetch both in parallel since they're independent
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
                    # Filter out leveraged tokens
                    if is_leveraged_token(sym):
                        continue
                    vol = ticker_vol.get(sym, 0)
                    if vol >= self.min_quote_volume:
                        coins.append(sym)

            coins.sort()
            self._discovered_symbols = coins
            logger.info(f"[Engine] Discovered {len(coins)} coins (min vol ${self.min_quote_volume:,.0f})")
            return coins
        except Exception as e:
            logger.warning(f"[Engine] Auto-discover failed: {e}. Using config symbols.")
            return self.symbols

    def get_active_symbols(self) -> list[str]:
        """Get symbols to scan — discovered or config."""
        if self.auto_discover and not self._discovered_symbols:
            return self.discover_coins()
        if self.auto_discover:
            return self._discovered_symbols
        return self.symbols

    # ---- Core screening ----

    def scan(self) -> dict:
        """
        Run a full screening cycle.

        Returns:
            dict with: ok, timestamp, elapsed, data, summary, scan_count
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
        logger.info(f"[Engine] Starting scan of {len(symbols)} coins...")

        try:
            # Fetch ticker data
            all_tickers = self.api.get_all_tickers()
            ticker_map = {t["symbol"]: t for t in all_tickers}

            results = []
            errors = 0

            # Helper to fetch klines for one symbol
            def fetch_klines_for_symbol(symbol: str) -> tuple[str, dict, Exception | None]:
                klines_by_tf = {}
                try:
                    for tf in self.timeframes:
                        klines_by_tf[tf] = self.api.get_klines(symbol, tf, self.kline_limit)
                    return symbol, klines_by_tf, None
                except Exception as e:
                    return symbol, klines_by_tf, e

            # Fetch klines concurrently (10 workers to respect rate limits)
            with ThreadPoolExecutor(max_workers=10) as executor:
                future_to_symbol = {
                    executor.submit(fetch_klines_for_symbol, sym): sym
                    for sym in symbols
                }
                for future in as_completed(future_to_symbol):
                    symbol, klines_by_tf, error = future.result()
                    if error:
                        logger.warning(f"[Engine] Error fetching klines for {symbol}: {error}")
                        errors += 1
                        continue

                    if not any(klines_by_tf.values()):
                        errors += 1
                        continue

                    # Get current price
                    price = float(ticker_map.get(symbol, {}).get("lastPrice", 0))
                    if price == 0 and klines_by_tf.get("15m"):
                        price = klines_by_tf["15m"][-1]["close"]

                    # Detect regime using 15m data
                    regime = {"regime": "SIDEWAYS"}
                    if klines_by_tf.get("15m"):
                        try:
                            regime_df = pd.DataFrame(klines_by_tf["15m"])
                            if len(regime_df) >= 50:
                                regime = self.regime_detector.detect(regime_df)
                        except Exception as e:
                            logger.warning(f"[Engine] Regime detection failed for {symbol}: {e}")
                    
                    # Get enhanced market microstructure data (L/S ratio, funding, OI, etc.)
                    enhanced_metrics = None
                    try:
                        # Only fetch for top 10 liquid coins to save API calls
                        top_symbols = set(self.config.get("symbols", [])[:10])
                        if symbol in top_symbols:
                            enhanced_metrics = self.enhanced_data.get_enhanced_metrics(symbol)
                            if enhanced_metrics and enhanced_metrics.get("compositeSignals"):
                                logger.debug(f"[Engine] Enhanced signals for {symbol}: {enhanced_metrics['compositeSignals']}")
                    except Exception as e:
                        logger.debug(f"[Engine] Enhanced data fetch failed for {symbol}: {e}")
                    
                    # Get adaptive RL weights for this regime
                    regime_type = regime.get("regime", "SIDEWAYS") if isinstance(regime, dict) else str(regime)
                    adaptive_params = {}
                    try:
                        adaptive_params = self.rl_optimizer.get_recommended_params(regime_type)
                    except Exception:
                        pass  # RL not available, use defaults
                    
                    # Score with adaptive RL parameters + enhanced metrics
                    score_result = self.scorer.score_coin(klines_by_tf, regime_type, adaptive_params, enhanced_metrics)

                    # Generate signal
                    coin_data = {
                        "symbol": symbol,
                        "price": price,
                        "klines": klines_by_tf.get("15m", []),
                        "regime": regime,
                        **score_result
                    }
                    signal_result = generate_signal(coin_data, self.config)
                    results.append(signal_result)

            elapsed = time.time() - start_time

            # Build summary
            long_count = sum(1 for r in results if r["signal"] == "LONG")
            short_count = sum(1 for r in results if r["signal"] == "SHORT")
            wait_count = sum(1 for r in results if r["signal"] == "WAIT")

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
                    "errors": errors
                }
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

            # Stage 2: Database Tracking (Signals + Outcome Check)
            ts = scan_result["timestamp"]
            try:
                # 1. Save new signals to DB
                self.db.save_signals(ts, results)

                # 2. Check outcomes of ALL open signals using full market prices
                # Reuse existing ticker_map - already have prices from all_tickers
                price_map = {t["symbol"]: float(t.get("lastPrice", 0)) for t in all_tickers}
                self.db.check_outcomes(price_map)

                # 3. Alerts (in-memory)
                alerts = self.alerter.check(results, ts)
                self._last_alerts = alerts
                if alerts:
                    logger.info(f"[Engine] {len(alerts)} signal alerts detected")
                
                # 4. RL: Update adaptive weights based on recent outcomes
                try:
                    rl_updates = self.rl_optimizer.update_weights()
                    if rl_updates:
                        logger.info(f"[Engine] RL: Updated weights for {len(rl_updates)} regimes")
                except Exception as rl_err:
                    logger.debug(f"[Engine] RL update skipped: {rl_err}")
                    
            except Exception as e:
                logger.warning(f"[Engine] DB/Alert/RL error: {e}")

            logger.info(
                f"[Engine] Scan complete: {len(results)} coins, "
                f"{long_count}L/{short_count}S/{wait_count}W in {elapsed:.1f}s"
            )

            return scan_result

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[Engine] Scan failed: {error_msg}")
            with self._lock:
                self._is_scanning = False
                self._last_error = error_msg
            return {
                "ok": False,
                "error": error_msg,
                "timestamp": datetime.now().isoformat()
            }

    # ---- Read cached results (non-blocking) ----

    def get_latest_scan(self) -> Optional[dict]:
        """Get the latest scan result from memory."""
        with self._lock:
            if not self._last_result:
                # Release lock before loading from file
                pass  # fall through to _load_cache outside lock

        # Try loading from cache file (outside lock)
        if not self._last_result:
            cached = self._load_cache()
            if cached:
                return cached

        return self._build_response()

    def get_signals(self) -> dict:
        """Get only coins with LONG or SHORT signals."""
        result = self.get_latest_scan()
        if result is None or not result.get("ok"):
            return {"ok": False, "error": "No scan data available"}

        signals = [r for r in result["data"] if r["signal"] in ("LONG", "SHORT")]
        result["data"] = signals
        result["summary"]["active_signals"] = len(signals)
        return result

    def get_coin_detail(self, symbol: str) -> Optional[dict]:
        """Get detail for a specific coin from last scan."""
        result = self.get_latest_scan()
        if result is None or not result.get("ok"):
            return None

        symbol = symbol.upper()
        for coin in result["data"]:
            if coin["symbol"] == symbol:
                return coin
        return None

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
                "last_error": self._last_error
            }

    def scan_single_symbol(self, symbol: str) -> dict:
        """
        Deep scan for a single specific coin.
        Robust: Handles 4H timeouts gracefully.
        """
        logger.info(f"[Engine] Scanning single coin: {symbol}")
        try:
            # 1. Fetch ticker
            ticker_data = self.api.get_ticker_24hr(symbol)
            if not ticker_data:
                return {"ok": False, "error": "Symbol not found"}
            if isinstance(ticker_data, list):
                if not ticker_data:
                    return {"ok": False, "error": "Empty ticker data"}
                ticker_data = ticker_data[0]

            price = float(ticker_data.get("lastPrice", 0))
            if price <= 0:
                return {"ok": False, "error": "Invalid price"}
            
            # 2. Fetch klines (Robust Loop)
            klines_by_tf = {}
            for tf in self.timeframes:
                try:
                    klines_by_tf[tf] = self.api.get_klines(symbol, tf, self.kline_limit)
                except Exception as e:
                    logger.warning(f"[Engine] {tf} fetch failed for {symbol}: {e}")
                    # Continue with other timeframes
            
            # Ensure we have at least primary data (15m)
            if not klines_by_tf.get("15m"):
                return {"ok": False, "error": "No kline data (15m missing)"}

            # 3. Score & Signal
            score_result = self.scorer.score_coin(klines_by_tf)

            # Detect regime using 15m data
            regime = {"regime": "SIDEWAYS"}
            if klines_by_tf.get("15m"):
                try:
                    regime_df = pd.DataFrame(klines_by_tf["15m"])
                    if len(regime_df) >= 50:
                        regime = self.regime_detector.detect(regime_df)
                except Exception as e:
                    logger.warning(f"[Engine] Regime detection failed for {symbol}: {e}")

            # Construct complete coin data object
            coin_data = {
                "symbol": symbol,
                "price": price,
                "klines": klines_by_tf.get("15m", []),
                "regime": regime,
                **score_result
            }
            signal_result = generate_signal(coin_data, self.config)
            
            return {"ok": True, "data": signal_result}
        except Exception as e:
            logger.error(f"[Engine] Single scan error for {symbol}: {e}")
            return {"ok": False, "error": str(e)}

    def clear_cache(self):
        """Clears the last scan result cache."""
        with self._lock:
            self._last_result = []
            logger.info("[Engine] Cache cleared.")

    def is_scanning(self) -> bool:
        """Check if a scan is currently running."""
        with self._lock:
            return self._is_scanning

    # ---- Alerts & Database ----

    def get_alerts(self, limit: int = 20) -> list[dict]:
        """Get recent alerts from database."""
        try:
            c = self.db.conn.cursor()
            c.execute("SELECT * FROM signals ORDER BY timestamp DESC LIMIT ?", (limit,))
            rows = c.fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"DB get_alerts error: {e}")
            return []

    def get_db_stats(self) -> dict:
        """Get database statistics."""
        return self.db.get_summary()

    def get_coin_history(self, symbol: str, limit: int = 50) -> list[dict]:
        """Get signal history for a specific coin."""
        return self.db.get_signal_history(symbol, limit)

    def get_daily_history(self) -> list[dict]:
        """Get daily aggregated stats."""
        return self.db.get_daily_stats()

    def get_calendar(self, year: int, month: int) -> list[dict]:
        """Get calendar view for a month."""
        return self.db.get_calendar_month(year, month)

    def get_signals_history(self, limit: int = 100) -> list[dict]:
        """Get signal history from database with outcomes."""
        return self.db.get_signals_with_outcomes(limit)

    def format_alerts_text(self, alerts: list[dict]) -> str:
        """Format alerts for Telegram/console."""
        return self.alerter.format_alerts(alerts)

    # ---- Schedule management ----

    def set_next_scan(self, timestamp: str):
        """Set the next scheduled scan time."""
        with self._lock:
            self._next_scan_time = timestamp

    # ---- Cache ----

    def _save_cache(self, data: dict):
        """Save scan result to file for persistence (without raw kline data)."""
        try:
            # Strip raw kline data to reduce cache size
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
                    "patterns_detected": item.get("patterns_detected", [])
                }
                compact_data.append(compact_item)

            cache_data = {
                "timestamp": data["timestamp"],
                "elapsed": data["elapsed_seconds"],
                "data": compact_data,
                "summary": data["summary"]
            }
            with open(self.cache_file, "w") as f:
                json.dump(cache_data, f)
        except Exception as e:
            logger.warning(f"[Engine] Failed to save cache: {e}")

    def _load_cache(self) -> Optional[dict]:
        """Load cached scan result from file."""
        try:
            if self.cache_file.exists():
                with open(self.cache_file, "r") as f:
                    cached = json.load(f)
                return {
                    "ok": True,
                    "timestamp": cached["timestamp"],
                    "elapsed_seconds": cached.get("elapsed", 0),
                    "data": cached["data"],
                    "summary": cached["summary"],
                    "from_cache": True
                }
        except Exception as e:
            logger.warning(f"[Engine] Failed to load cache: {e}")
        return None

    def _build_response(self) -> Optional[dict]:
        """Build response from in-memory result."""
        with self._lock:
            if not self._last_result:
                return None

            long_count = sum(1 for r in self._last_result if r["signal"] == "LONG")
            short_count = sum(1 for r in self._last_result if r["signal"] == "SHORT")
            wait_count = sum(1 for r in self._last_result if r["signal"] == "WAIT")

            return {
                "ok": True,
                "timestamp": self._last_scan_time,
                "elapsed_seconds": round(self._last_elapsed, 1),
                "data": self._last_result,
                "summary": {
                    "total": len(self._last_result),
                    "long": long_count,
                    "short": short_count,
                    "wait": wait_count
                },
                "from_cache": False
            }

    # ---- Cleanup ----

    def close(self):
        """Close resources."""
        if self._api:
            self._api.close()
        if self.db:
            self.db.close()
        logger.info("[Engine] Closed")
