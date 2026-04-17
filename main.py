"""
Coin Screener Bot — Main Entry Point
Deep screening + signal generation + Telegram bot.
"""
import sys
import time
import yaml
import logging
import signal as sig
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.binance_api import BinanceFuturesAPI
from src.scorer import Scorer
from src.signals import generate_signal
from src.display import print_screen_result, print_status, print_error

# ---- Config ----

def load_config(path: str = "config.yaml") -> dict:
    """Load configuration from YAML file."""
    with open(path, "r") as f:
        return yaml.safe_load(f)


def setup_logging(config: dict):
    """Setup logging to file and console."""
    log_config = config.get("logging", {})
    log_file = log_config.get("file", "data/screener.log")
    log_level = log_config.get("level", "INFO")

    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ]
    )


# ---- Screening Engine ----

class CoinScreener:
    """Main screening engine."""

    def __init__(self, config: dict):
        self.config = config
        self.api = BinanceFuturesAPI()
        self.scorer = Scorer(config)
        self.symbols = config.get("symbols", [])
        self.timeframes = config.get("timeframes", ["15m", "1h", "4h"])
        self.kline_limit = config.get("scan", {}).get("kline_limit", 200)
        self.logger = logging.getLogger(__name__)

    def run(self) -> list[dict]:
        """
        Run full screening cycle.

        Returns:
            List of signal dicts for all coins.
        """
        start_time = time.time()
        self.logger.info(f"Starting scan of {len(self.symbols)} coins...")

        # Fetch 24hr ticker for current prices
        try:
            all_tickers = self.api.get_all_tickers()
            ticker_map = {t["symbol"]: t for t in all_tickers}
        except Exception as e:
            self.logger.error(f"Failed to fetch tickers: {e}")
            return []

        results = []

        for symbol in self.symbols:
            try:
                # Get klines for all timeframes
                klines_by_tf = {}
                for tf in self.timeframes:
                    klines = self.api.get_klines(symbol, tf, self.kline_limit)
                    klines_by_tf[tf] = klines

                if not any(klines_by_tf.values()):
                    continue

                # Get price
                price = float(ticker_map.get(symbol, {}).get("lastPrice", 0))
                if price == 0 and klines_by_tf.get("15m"):
                    price = klines_by_tf["15m"][-1]["close"]

                # Score
                score_result = self.scorer.score_coin(klines_by_tf)

                # Generate signal
                coin_data = {
                    "symbol": symbol,
                    "price": price,
                    "klines": klines_by_tf.get("15m", []),
                    **score_result
                }
                signal_result = generate_signal(coin_data, self.config)
                results.append(signal_result)

            except Exception as e:
                self.logger.warning(f"Error processing {symbol}: {e}")
                continue

        elapsed = time.time() - start_time
        self.logger.info(f"Scan complete: {len(results)} coins in {elapsed:.1f}s")

        return results

    def close(self):
        """Cleanup resources."""
        self.api.close()


# ---- Main Runner ----

def main():
    """Main entry point."""
    # Load config
    config = load_config()
    setup_logging(config)
    logger = logging.getLogger(__name__)

    print_status("🚀 Coin Screener Bot starting...")

    # Initialize screener
    screener = CoinScreener(config)
    scan_count = 0

    # Handle graceful shutdown
    running = True

    def shutdown_handler(signum, frame):
        nonlocal running
        running = False
        print_status("\n👋 Shutting down...")
        screener.close()

    sig.signal(sig.SIGINT, shutdown_handler)
    sig.signal(sig.SIGTERM, shutdown_handler)

    # Run screening loop
    interval = config.get("scan", {}).get("interval_minutes", 15)
    print_status(f"📊 Auto-scan every {interval} minutes. Press Ctrl+C to stop.\n")

    while running:
        try:
            scan_count += 1
            print_status(f"🔄 Scan #{scan_count} — {datetime.now().strftime('%H:%M:%S')}")

            results = screener.run()

            if results:
                print_screen_result(results, elapsed_seconds=0)  # elapsed already in results
            else:
                print_error("No results returned.")

            # Wait for next scan
            next_scan = datetime.now().strftime("%H:%M:%S")
            print_status(f"⏳ Next scan in {interval} minutes (at {next_scan} + {interval}m)...")

            # Sleep in small increments for responsive shutdown
            sleep_seconds = interval * 60
            for _ in range(sleep_seconds):
                if not running:
                    break
                time.sleep(1)

        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Scan loop error: {e}")
            time.sleep(30)

    print_status("👋 Coin Screener stopped.")


if __name__ == "__main__":
    main()
