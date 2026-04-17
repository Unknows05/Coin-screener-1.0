# Code Review: Coin Screener

**Date:** 2026-04-14  
**Target:** /home/febrian/Desktop/coin-screener (local codebase review)  
**Language:** Python (FastAPI + pandas + requests)

## Diff Statistics

Not a git repo — reviewing full codebase.  
13 Python files, ~1500 lines total.

## Deterministic Analysis (Ruff)

| Check | Result |
|-------|--------|
| Unused imports | 8 files (api.py, main.py, alerter.py, display.py, engine.py, patterns.py, scorer.py) |
| E701 multi-statement on one line | 12 occurrences across 5 files |
| E722 bare except | 1 in signals.py |
| F841 unused variable | 1 in scorer.py (`weight`) |
| Invalid syntax | engine_temp_fix.py (orphaned method fragment) |

---

## Findings (After Verification & Deduplication)

### Critical

**1. RegimeDetector is dead code — regime always hardcoded to SIDEWAYS**
- **File:** src/engine.py:354, src/engine_temp_fix.py:43
- **Source:** [review]
- **Issue:** `RegimeDetector` class exists in `src/regime.py` (fully implemented, 170 lines) but is never imported or used. In `engine.py:scan_single_symbol()` and the temp fix, regime is hardcoded as `{"regime": "SIDEWAYS"}`. The main `scan()` method in engine.py also never calls RegimeDetector. Signals are generated with regime="SIDEWAYS" regardless of actual market conditions.
- **Impact:** Regime-based signal reasoning is completely non-functional. The `regime` field in API responses is always "SIDEWAYS" (or whatever scorer passes through), making regime filtering in the dashboard meaningless.
- **Suggested fix:** Either integrate RegimeDetector into the scan pipeline, or remove regime.py and all regime references from signals.py, display.py, database.py, and the dashboard.
- **Severity:** Critical

**2. Sequential N+1 API calls — 90 synchronous HTTP requests per scan**
- **File:** src/engine.py:119-131
- **Source:** [review]
- **Issue:** The `scan()` method loops over 30 symbols × 3 timeframes, making 90 sequential `get_klines()` calls. Each call blocks on network I/O. With ~100-200ms latency per call, this adds 9-18 seconds of pure network wait time.
- **Impact:** Scan latency is dominated by sequential HTTP calls. This is the single biggest performance bottleneck.
- **Suggested fix:** Use `concurrent.futures.ThreadPoolExecutor` with a semaphore (e.g., 10 concurrent) to fetch klines in parallel while respecting Binance rate limits. Could reduce scan time from ~15-30s to ~2-5s.
- **Severity:** Critical

**3. No rate limiting or backoff for Binance API**
- **File:** src/binance_api.py:24-32
- **Source:** [review]
- **Issue:** The `_get()` method has a single retry with blocking `time.sleep(1)` and no exponential backoff. It does not track or respect Binance rate limit headers (`X-MBX-USED-WEIGHT`). With 90 calls per scan, hitting rate limits is likely.
- **Impact:** When rate-limited, the code raises an unhandled exception on the second attempt, causing the entire scan to fail. Also, `time.sleep(1)` blocks the thread in an async context.
- **Suggested fix:** Implement exponential backoff with jitter. Add a rate limiter (token bucket) to throttle requests before hitting the limit. Replace `time.sleep` with async-compatible sleep or run blocking calls in a thread pool.
- **Severity:** Critical

**4. `_recalc_daily_stats` recalculates ALL dates on every write**
- **File:** src/database.py:87-104
- **Source:** [review]
- **Issue:** Every call to `save_signals()` or `check_outcomes()` triggers `_recalc_daily_stats()`, which fetches ALL distinct scan dates and recalculates stats for every single one. As the database grows, this becomes progressively slower.
- **Impact:** Unnecessary I/O on every scan cycle. Performance degrades linearly with historical data size.
- **Suggested fix:** Only recalculate stats for the current `scan_date`. Add an index on `signals(scan_date)`.
- **Severity:** Critical

**5. `RegimeDetector` imported but never used in engine.py**
- **File:** src/engine.py:14-19
- **Source:** [linter] + [review]
- **Issue:** `format_telegram_message` is imported but never used. More importantly, `RegimeDetector` from `src/regime.py` is not imported at all — the entire 170-line module is dead code.
- **Impact:** Dead code adds maintenance burden and confusion.
- **Suggested fix:** Either use RegimeDetector in the scan pipeline, or remove it. Remove unused imports.
- **Severity:** Critical

### Suggestion

**6. `asyncio.create_task(engine.scan())` fire-and-forget without tracking**
- **File:** api.py:114, api.py:193
- **Source:** [review]
- **Issue:** Both `/api/scan` and `/api/refresh` use `asyncio.create_task(engine.scan())` without storing the task reference. If the scan raises an exception, it becomes an unhandled task exception. Race condition: rapid calls to `/api/scan` could trigger overlapping scans.
- **Impact:** Unhandled exceptions lost; potential for overlapping scans corrupting shared state.
- **Suggested fix:** Store task references in a set, add a done callback to log exceptions, and move the `is_scanning()` check inside the task.

**7. `/api/volatile` blocks event loop with synchronous `requests.get()`**
- **File:** api.py:169-171
- **Source:** [review]
- **Issue:** The `/api/volatile` endpoint uses `requests.get(url, timeout=10)` — a blocking call inside an async endpoint. This blocks the entire event loop for up to 10 seconds.
- **Impact:** All other API requests are blocked during this call, causing timeouts for dashboard users.
- **Suggested fix:** Use `asyncio.to_thread()` to run the blocking call in a thread pool, or use `httpx` for async HTTP.

**8. Unused imports across 8 files (ruff F401)**
- **Files:** api.py (os, HTTPException, BackgroundTasks, print_screen_result), main.py (os), alerter.py (datetime), display.py (Text), engine.py (format_telegram_message), patterns.py (numpy), scorer.py (numpy)
- **Source:** [linter]
- **Impact:** Code cleanliness, minor import overhead.
- **Suggested fix:** Remove all unused imports.

**9. Multiple statements on one line (ruff E701) — 12 occurrences**
- **Files:** api.py:286-292, database.py:96, engine.py:325-331, engine_temp_fix.py, scorer.py:32/65, signals.py:88-103
- **Source:** [linter]
- **Issue:** Patterns like `if condition: continue` on a single line.
- **Impact:** Style violation, readability.
- **Suggested fix:** Split into two lines.

**10. Bare `except` in signals.py (ruff E722)**
- **File:** src/signals.py:96
- **Source:** [linter]
- **Issue:** `except:` catches `SystemExit` and `KeyboardInterrupt` too.
- **Impact:** Masks unexpected errors, prevents graceful shutdown.
- **Suggested fix:** Use `except Exception:`.

**11. `engine_temp_fix.py` is an orphaned method fragment**
- **File:** src/engine_temp_fix.py
- **Source:** [review]
- **Issue:** This file contains a single method (`scan_single_symbol`) with invalid syntax (orphaned `def` with wrong indentation). It appears to be a patch that was never merged into `engine.py`. The actual `engine.py` already has a similar `scan_single_symbol` method.
- **Impact:** Dead code, ruff reports syntax errors.
- **Suggested fix:** Either merge the improvements into engine.py's existing method, or delete this file.

**12. Bollinger Bands computed twice in scorer.py**
- **File:** src/scorer.py:47-48, 108-117
- **Source:** [review]
- **Issue:** `_calc_bollinger_width(df)` and `_calc_bollinger_upper(df)` independently compute the same SMA(20) and STD(20).
- **Impact:** Wastes ~2x CPU for BB computation per coin per timeframe.
- **Suggested fix:** Compute BB components once and return all derived values.

**13. `weight` variable assigned but never used (ruff F841)**
- **File:** src/scorer.py:36
- **Source:** [linter]
- **Issue:** `weight = self.tf_weights.get(tf, 0)` is assigned but never referenced in the loop body.
- **Impact:** Dead code.
- **Suggested fix:** Remove the assignment or use the variable.

**14. Redundant rolling window computations in alpha.py**
- **File:** src/alpha.py
- **Source:** [review]
- **Issue:** Multiple alpha factors recompute identical rolling windows (e.g., `close.rolling(20).mean()` and `close.rolling(20).std()` appear in MR-1, MR-5, VOLAT-4, VOLAT-6).
- **Impact:** Dozens of redundant O(N) passes over the same data per coin per timeframe.
- **Suggested fix:** Precompute all unique rolling windows once and pass cached Series to each factor.

**15. Slow `rolling().apply(lambda ...)` in alpha.py MOM-3**
- **File:** src/alpha.py:121
- **Source:** [review]
- **Issue:** `close.rolling(20).apply(lambda x: x.iloc[:-1].mean())` uses a Python lambda inside rolling.apply — not vectorized.
- **Impact:** Runs in a Python loop over every window, orders of magnitude slower than vectorized equivalent.
- **Suggested fix:** Replace with `close.shift(1).rolling(19).mean()`.

**16. SQLite connection not thread-safe**
- **File:** src/database.py:23
- **Source:** [review]
- **Issue:** `sqlite3.connect(..., check_same_thread=False)` allows concurrent access from multiple threads. SQLite connections are not thread-safe by default. Concurrent writes can cause `database is locked` errors.
- **Impact:** Database write failures under concurrent access, losing signal history.
- **Suggested fix:** Add a threading lock around all database writes, enable WAL mode (`PRAGMA journal_mode=WAL`).

**17. Alerter stores shallow references to result dicts**
- **File:** src/alerter.py:64
- **Source:** [review]
- **Issue:** `self._previous_signals = current_map.copy()` stores references to the same dict objects, not deep copies. If the caller later mutates the result dicts, the alerter's "previous" state becomes corrupted.
- **Impact:** Signal change detection compares against corrupted previous state.
- **Suggested fix:** Use `copy.deepcopy(current_map)`.

**18. API server binds to 0.0.0.0 with no authentication**
- **File:** api.py:220
- **Source:** [review]
- **Issue:** `uvicorn.run(host="0.0.0.0", ...)` binds to all network interfaces. No authentication or rate limiting.
- **Impact:** Anyone on the same network can access trading signals and system status.
- **Suggested fix:** Bind to `127.0.0.1` for local-only access, or add API key authentication.

---

### Nice to have

**19. Missing SQLite indexes**
- **File:** src/database.py:33-53
- **Source:** [review]
- **Issue:** No indexes on the `signals` table. Queries for dedup checks, outcome checks, and alert retrieval do full table scans.
- **Suggested fix:** Add composite indexes on `(symbol, timestamp, signal)` and `(result)`.

**20. `pd.concat` used for ATR instead of vectorized NumPy**
- **File:** src/alpha.py:237-242, src/signals.py:76-81
- **Source:** [review]
- **Issue:** `pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)` creates a temporary DataFrame. `np.maximum.reduce()` is faster.
- **Suggested fix:** Replace with `np.maximum.reduce([tr1.values, tr2.values, tr3.values])`.

**21. Cache serialization writes full scan result every cycle**
- **File:** src/engine.py:209-218
- **Source:** [review]
- **Issue:** `_save_cache()` serializes the entire scan result including raw kline data to JSON on every scan.
- **Suggested fix:** Only cache signal results (symbol, price, signal, confidence, SL, TP) without raw kline data.

---

## Verdict

**Request changes** — Has critical issues that affect core functionality:

1. **RegimeDetector is dead code** — the entire regime detection module is never used, yet regime information is displayed in the dashboard and stored in the database. This is misleading and wastes development effort.
2. **Performance is dominated by sequential API calls** — converting to concurrent fetches would reduce scan time by 5-10x.
3. **No rate limiting** — the current retry logic is fragile and will fail catastrophically when Binance rate-limits.

These are not bugs that cause crashes, but they represent significant technical debt and performance issues that should be addressed before relying on this system for trading decisions.

---

Tip: type `fix these issues` to apply fixes interactively.
