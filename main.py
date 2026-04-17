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

from src.engine import ScreeningEngine
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


# ---- Main Runner ----

def main():
    """Main entry point."""
    # Load config
    config = load_config()
    setup_logging(config)
    logger = logging.getLogger(__name__)

    print_status("🚀 Coin Screener Bot starting...")

    # Initialize screening engine (single source of truth)
    screener = ScreeningEngine(config, cache_dir="data")
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

            result = screener.scan()

            if result.get("ok"):
                print_screen_result(result["data"], elapsed_seconds=result["elapsed_seconds"])
            else:
                print_error(f"Scan failed: {result.get('error', 'Unknown error')}")

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
