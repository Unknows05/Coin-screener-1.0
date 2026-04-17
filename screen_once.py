#!/usr/bin/env python3
"""Run a single screening cycle and display results."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import yaml
from src.binance_api import BinanceFuturesAPI
from src.scorer import Scorer
from src.signals import generate_signal
from src.display import print_screen_result, print_error


def main():
    config = yaml.safe_load(open("config.yaml"))
    api = BinanceFuturesAPI()
    scorer = Scorer(config)

    print("🔍 Fetching market data...")
    start = time.time()

    try:
        tickers = api.get_all_tickers()
    except Exception as e:
        print_error(f"Failed to fetch tickers: {e}")
        return

    ticker_map = {t["symbol"]: t for t in tickers}
    results = []

    for sym in config["symbols"]:
        try:
            klines = {}
            for tf in config["timeframes"]:
                klines[tf] = api.get_klines(sym, tf, config["scan"]["kline_limit"])

            if not any(klines.values()):
                continue

            price = float(ticker_map.get(sym, {}).get("lastPrice", 0))
            if price == 0 and klines.get("15m"):
                price = klines["15m"][-1]["close"]

            score_result = scorer.score_coin(klines)
            coin_data = {
                "symbol": sym, "price": price,
                "klines": klines.get("15m", []), **score_result
            }
            results.append(generate_signal(coin_data, config))

        except Exception as e:
            print(f"⚠️  {sym}: {e}")

    elapsed = time.time() - start
    api.close()

    if results:
        print_screen_result(results, elapsed)
    else:
        print_error("No results returned.")


if __name__ == "__main__":
    main()
