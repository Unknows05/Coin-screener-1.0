"""
Binance Futures REST API Connector — Public endpoints only (no API key needed).
Base URL: https://fapi.binance.com (mainnet)
"""
import logging
import time
import random
import requests
from typing import Optional
import threading

logger = logging.getLogger(__name__)

BASE_URL = "https://fapi.binance.com"
MAX_RETRIES = 5
RATE_LIMIT_CALLS = 120  # Binance allows ~120 calls per 60 seconds for IP weight
RATE_LIMIT_WINDOW = 60  # seconds


class RateLimiter:
    """Token bucket rate limiter for Binance API."""

    def __init__(self, calls: int = RATE_LIMIT_CALLS, period: float = RATE_LIMIT_WINDOW):
        self.calls = calls
        self.period = period
        self.tokens = calls
        self.last_update = time.time()
        self._lock = threading.Lock()

    def acquire(self):
        """Acquire a token, blocking if necessary."""
        with self._lock:
            while True:
                now = time.time()
                elapsed = now - self.last_update
                self.tokens = min(self.calls, self.tokens + elapsed * (self.calls / self.period))
                self.last_update = now

                if self.tokens >= 1:
                    self.tokens -= 1
                    return

                # Sleep until we have a token
                sleep_time = (1 - self.tokens) * (self.period / self.calls)
                time.sleep(sleep_time)


class BinanceFuturesAPI:
    """Connector for Binance USDT-M Futures public API endpoints."""

    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "CoinScreener/1.0"
        })
        self._rate_limiter = RateLimiter()
        logger.info(f"[BinanceAPI] Connected to {self.base_url}")

    def _get(self, path: str, params: Optional[dict] = None, timeout: int = 15) -> dict:
        """Make GET request with rate limiting and exponential backoff with jitter.
        
        Fast-fail on 4xx client errors (401/403/429) — no retry on auth/permanent errors.
        Only retry on network errors and 5xx server errors.
        """
        url = f"{self.base_url}{path}"
        last_error = None

        for attempt in range(MAX_RETRIES):
            try:
                self._rate_limiter.acquire()

                resp = self.session.get(url, params=params, timeout=timeout)
                
                # Fast-fail on client errors (auth, forbidden, not found)
                # These won't resolve with retries
                if resp.status_code in (401, 403, 404, 400):
                    logger.warning(
                        f"[BinanceAPI] Client error {resp.status_code} for {path} "
                        f"(no retry): {resp.text[:200]}"
                    )
                    return {"error": f"client_error_{resp.status_code}", "msg": resp.text[:200]}
                
                if resp.status_code == 429:
                    # Rate limited — single retry after 60s backoff
                    if attempt == 0:
                        logger.warning(f"[BinanceAPI] Rate limited (429), backing off 60s")
                        time.sleep(60)
                        continue
                    return {"error": "rate_limited", "msg": "429 Too Many Requests"}
                
                resp.raise_for_status()
                return resp.json()
            except (requests.Timeout, requests.ConnectionError) as e:
                # Network errors — retry with backoff
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    base_delay = 0.5
                    delay = min(base_delay * (2 ** attempt) + random.uniform(0, 0.5), 30)
                    logger.warning(
                        f"[BinanceAPI] Network error (attempt {attempt + 1}/{MAX_RETRIES}): "
                        f"{e}. Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
            except requests.HTTPError as e:
                status = e.response.status_code if e.response else 0
                if 400 <= status < 500:
                    # Client errors — don't retry
                    logger.warning(f"[BinanceAPI] HTTP {status} for {path} (no retry)")
                    return {"error": f"http_error_{status}", "msg": str(e)[:200]}
                # 5xx — server error, retry
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    delay = min(2 ** attempt + random.uniform(0, 0.5), 30)
                    logger.warning(
                        f"[BinanceAPI] Server error {status} (attempt {attempt + 1}/{MAX_RETRIES}): "
                        f"{e}. Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
            except (requests.RequestException, ValueError) as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    delay = min(0.5 * (2 ** attempt) + random.uniform(0, 0.5), 15)
                    logger.warning(
                        f"[BinanceAPI] Request failed (attempt {attempt + 1}/{MAX_RETRIES}): "
                        f"{e}. Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)

        raise Exception(
            f"[BinanceAPI] All {MAX_RETRIES} attempts failed. Last error: {last_error}"
        )

    def get_exchange_info(self) -> dict:
        """Get exchange info — all USDT-M Futures symbols."""
        return self._get("/fapi/v1/exchangeInfo")

    def get_all_symbols(self) -> list[str]:
        """Get list of all active USDT-M trading symbols."""
        data = self.get_exchange_info()
        symbols = []
        for s in data.get("symbols", []):
            if (s.get("contractType") == "PERPETUAL"
                    and s.get("status") == "TRADING"
                    and s.get("quoteAsset") == "USDT"):
                symbols.append(s["symbol"])
        return symbols

    def get_ticker_24hr(self, symbol: Optional[str] = None) -> list[dict]:
        """Get 24hr ticker price change statistics."""
        params = {"symbol": symbol} if symbol else {}
        return self._get("/fapi/v1/ticker/24hr", params)

    def get_all_tickers(self) -> list[dict]:
        """Get all 24hr tickers in one call."""
        return self.get_ticker_24hr()

    def get_klines(self, symbol: str, interval: str = "15m",
                   limit: int = 200) -> list[dict]:
        """
        Get kline/candlestick data.
        Includes validation for Binance API error responses.
        """
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": min(limit, 1500)
        }
        raw = self._get("/fapi/v1/klines", params)

        # Binance sometimes returns error dict with 200 OK status
        if isinstance(raw, dict) and "code" in raw:
            raise Exception(f"Binance API Error: {raw.get('msg', raw)}")

        # Binance returns: [open_time, open, high, low, close, volume, ...]
        klines = []
        for k in raw:
            klines.append({
                "open_time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": k[6],
                "quote_volume": float(k[7]),
                "trades": k[8],
            })
        return klines

    def get_funding_rate(self, symbol: str, limit: int = 10) -> list[dict]:
        """Get funding rate history."""
        params = {
            "symbol": symbol.upper(),
            "limit": min(limit, 1000)
        }
        return self._get("/fapi/v1/fundingRate", params)

    def get_mark_price(self, symbol: Optional[str] = None) -> list[dict]:
        """Get mark price and funding rate for all or single symbol."""
        params = {"symbol": symbol} if symbol else {}
        return self._get("/fapi/v1/premiumIndex", params)

    def get_open_interest(self, symbol: str) -> dict:
        """Get current open interest for a symbol."""
        return self._get("/fapi/v1/openInterest", {"symbol": symbol.upper()})

    def get_server_time(self) -> int:
        """Get server time in milliseconds."""
        data = self._get("/fapi/v1/time")
        return data.get("serverTime", 0)

    def close(self):
        """Close the HTTP session."""
        self.session.close()
        logger.info("[BinanceAPI] Session closed")
