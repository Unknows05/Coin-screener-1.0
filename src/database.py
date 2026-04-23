"""
Database — SQLite for signal tracking and daily statistics.
Optimized for Outcome Tracking (Stage 2).
"""
import sqlite3
import logging
import threading
from datetime import datetime
from pathlib import Path

from src.utils import calculate_win_rate, calculate_pnl_pct

logger = logging.getLogger(__name__)

class ScreenerDB:
    """SQLite database for signal history and daily stats."""

    def __init__(self, db_path: str = "data/screener.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._write_lock = threading.Lock()  # Thread-safe writes
        self._init_tables()
        self._enable_wal_mode()
        logger.info(f"[DB] Initialized at {self.db_path}")

    def _enable_wal_mode(self):
        """Enable WAL mode for better concurrent access."""
        c = self.conn.cursor()
        c.execute("PRAGMA journal_mode=WAL")
        result = c.fetchone()
        if result and result[0] == "wal":
            logger.info("[DB] WAL mode enabled")

    def _init_tables(self):
        """Create tables if not exist."""
        c = self.conn.cursor()
        # Signals Table - with unique constraint for deduplication
        c.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                signal TEXT NOT NULL,
                entry_price REAL,
                sl REAL,
                tp REAL,
                confidence INTEGER,
                regime TEXT,
                result TEXT DEFAULT 'OPEN',
                exit_price REAL,
                exit_timestamp TEXT,
                exit_reason TEXT,
                final_price REAL,
                scan_date TEXT,
                UNIQUE(symbol, timestamp, signal)
            )
        """)
        # Daily Stats Table
        c.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                scan_date TEXT PRIMARY KEY,
                total_signals INTEGER DEFAULT 0,
                long_count INTEGER DEFAULT 0,
                short_count INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                open_count INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                updated_at TEXT
            )
        """)
        # Indexes for performance
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_symbol_timestamp
            ON signals(symbol, timestamp, signal)
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_result
            ON signals(result)
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_scan_date
            ON signals(scan_date)
        """)
        self.conn.commit()

    def save_signals(self, timestamp: str, results: list[dict]):
        """Save new signals to database."""
        c = self.conn.cursor()
        scan_date = self.scan_date_from_ts(timestamp)
        count = 0

        # Batch insert with IGNORE for duplicates (more efficient than N+1 queries)
        signals_to_insert = []
        for r in results:
            sig = r.get("signal", "WAIT")
            if sig in ("LONG", "SHORT"):
                signals_to_insert.append((
                    timestamp, r["symbol"], sig, r.get("entry"), r.get("sl"),
                    r.get("tp"), r.get("confidence", 0), r.get("regime", ""), scan_date
                ))

        if signals_to_insert:
            # Use INSERT OR IGNORE with UNIQUE constraint check at DB level
            c.executemany(
                """INSERT OR IGNORE INTO signals
                   (timestamp, symbol, signal, entry_price, sl, tp, confidence, regime, scan_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                signals_to_insert
            )
            count = c.rowcount
            self.conn.commit()

        if count:
            self._recalc_daily_stats(scan_date)
            logger.info(f"[DB] Saved {count} new signals")

    def check_outcomes(self, prices: dict[str, float]):
        """Check all OPEN signals against current prices for SL/TP hits."""
        c = self.conn.cursor()

        # Fetch all OPEN or PENDING signals (only active trades)
        c.execute("""
            SELECT id, symbol, signal, sl, tp, entry_price, scan_date
            FROM signals
            WHERE result IN ('OPEN', 'PENDING')
        """)
        open_signals = c.fetchall()

        updated = 0
        now = datetime.now()
        dates_to_recalc = set()

        updates = []
        for row in open_signals:
            sid = row["id"]
            sym = row["symbol"]
            cur_price = prices.get(sym)
            if cur_price is None or cur_price <= 0:
                continue

            signal_type = row["signal"]
            entry = row["entry_price"]
            sl = row["sl"]
            tp = row["tp"]

            if sl is None or tp is None or entry is None:
                continue

            result = self._check_signal_outcome(signal_type, cur_price, sl, tp)

            if result:
                exit_ts = now.isoformat()
                reason = "STOP LOSS HIT" if result == "LOSS" else "TAKE PROFIT HIT"
                updates.append((result, cur_price, exit_ts, reason, cur_price, sid))
                dates_to_recalc.add(row["scan_date"])
                updated += 1
                logger.info(f"[DB] Signal #{sid} {sym} {signal_type} -> {result} at {cur_price:.4f} ({reason})")

        if updates:
            c.executemany(
                """UPDATE signals
                   SET result=?, exit_price=?, exit_timestamp=?,
                       exit_reason=?, final_price=?
                   WHERE id=?""",
                updates
            )
            self.conn.commit()
            # Recalc only affected dates
            for date in dates_to_recalc:
                self._recalc_daily_stats(date)
            logger.info(f"[DB] Updated {updated} signal outcomes")
        return updated

    def _check_signal_outcome(self, signal_type: str, cur_price: float, sl: float, tp: float) -> str | None:
        """Check if SL or TP was hit for a signal. Returns 'WIN', 'LOSS', or None."""
        if signal_type == "LONG":
            if cur_price <= sl:
                return "LOSS"
            elif cur_price >= tp:
                return "WIN"
        elif signal_type == "SHORT":
            if cur_price >= sl:
                return "LOSS"
            elif cur_price <= tp:
                return "WIN"
        return None

    def _recalc_daily_stats(self, date: str = None):
        """Recalculate stats for a specific date (defaults to today)."""
        c = self.conn.cursor()
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        c.execute("SELECT COUNT(*) as total, SUM(signal='LONG') as longs, "
                  "SUM(signal='SHORT') as shorts, SUM(result='WIN') as wins, "
                  "SUM(result='LOSS') as losses, SUM(result='OPEN') as opens FROM signals WHERE scan_date=?", (date,))
        row = c.fetchone()

        total = row["total"] or 0
        wins = row["wins"] or 0
        losses = row["losses"] or 0
        opens = row["opens"] or 0
        wr = calculate_win_rate(wins, losses)

        c.execute("INSERT OR REPLACE INTO daily_stats (scan_date, total_signals, long_count, "
                  "short_count, wins, losses, open_count, win_rate, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (date, total, row["longs"] or 0, row["shorts"] or 0, wins, losses, opens, wr, datetime.now().isoformat()))
        self.conn.commit()

    def get_summary(self) -> dict:
        """Get overall statistics."""
        c = self.conn.cursor()
        c.execute("SELECT SUM(result='WIN') as wins, SUM(result='LOSS') as losses, "
                  "SUM(result='OPEN') as opens FROM signals")
        row = c.fetchone()
        wins = row["wins"] or 0
        losses = row["losses"] or 0
        wr = calculate_win_rate(wins, losses)
        return {"wins": wins, "losses": losses, "open": row["opens"] or 0, "win_rate": wr}

    def scan_date_from_ts(self, timestamp: str) -> str:
        return datetime.fromisoformat(timestamp).strftime("%Y-%m-%d")

    def get_signal_history(self, symbol: str, limit: int = 50) -> list[dict]:
        """Get signal history for a specific coin."""
        try:
            c = self.conn.cursor()
            c.execute(
                "SELECT * FROM signals WHERE symbol=? ORDER BY timestamp DESC LIMIT ?",
                (symbol, limit)
            )
            rows = c.fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"[DB] get_signal_history error: {e}")
            return []

    def get_daily_stats(self) -> list[dict]:
        """Get daily aggregated stats."""
        try:
            c = self.conn.cursor()
            c.execute("SELECT * FROM daily_stats ORDER BY scan_date DESC")
            rows = c.fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"[DB] get_daily_stats error: {e}")
            return []

    def get_calendar_month(self, year: int, month: int) -> list[dict]:
        """Get calendar data for a specific month."""
        try:
            c = self.conn.cursor()
            # Format month for LIKE query (e.g., "2026-04-%")
            month_prefix = f"{year}-{month:02d}"
            c.execute(
                "SELECT * FROM daily_stats WHERE scan_date LIKE ? ORDER BY scan_date",
                (f"{month_prefix}%",)
            )
            rows = c.fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"[DB] get_calendar_month error: {e}")
            return []

    def get_signals_with_outcomes(self, limit: int = 500, result_filter: str = None, days: int = None) -> list[dict]:
        """
        Get signal history with SL/TP outcomes.
        Args:
            limit: Max signals to return
            result_filter: 'closed' for WIN/LOSS only, 'open' for OPEN only, None for all
            days: Limit to last N days
        """
        try:
            c = self.conn.cursor()
            query = """
                SELECT id, timestamp, symbol, signal, entry_price, sl, tp,
                    confidence, regime, result, exit_price, exit_timestamp,
                    exit_reason, final_price, scan_date
                FROM signals
                WHERE signal IN ('LONG', 'SHORT')
            """
            params = []

            if days:
                query += " AND timestamp >= datetime('now', ?)"
                params.append(f"-{days} days")

            if result_filter == 'closed':
                query += " AND result IN ('WIN', 'LOSS')"
            elif result_filter == 'open':
                query += " AND result IN ('OPEN', 'PENDING')"

            if result_filter == 'closed':
                query += " ORDER BY timestamp DESC LIMIT ?"
            else:
                query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            c.execute(query, params)
            rows = c.fetchall()
            signals = []
            for row in rows:
                sig = dict(row)
                entry = sig.get('entry_price', 0)
                exit_p = sig.get('exit_price') or sig.get('final_price')
                result = sig.get('result', 'OPEN')

                if result in ('WIN', 'LOSS') and entry and exit_p:
                    sig['pnl_pct'] = round(calculate_pnl_pct(entry, exit_p, sig.get('signal', '')), 2)
                else:
                    sig['pnl_pct'] = 0.0

                signals.append(sig)
            return signals
        except Exception as e:
            logger.error(f"[DB] get_signals_with_outcomes error: {e}")
            return []

    def get_daily_performance(self, days: int = 7) -> list[dict]:
        """Get daily performance stats with SL/TP averages for time ranges."""
        try:
            c = self.conn.cursor()
            c.execute("""
                SELECT
                    scan_date,
                    COUNT(*) as total,
                    SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) as losses,
                    SUM(CASE WHEN result IN ('OPEN','PENDING') THEN 1 ELSE 0 END) as opens,
                    ROUND(AVG(CASE WHEN result IN ('WIN','LOSS') AND sl IS NOT NULL AND entry_price > 0
                        THEN ABS(sl - entry_price) / entry_price * 100 END), 2) as avg_sl_pct,
                    ROUND(AVG(CASE WHEN result IN ('WIN','LOSS') AND tp IS NOT NULL AND entry_price > 0
                        THEN ABS(tp - entry_price) / entry_price * 100 END), 2) as avg_tp_pct,
                    SUM(CASE WHEN signal='LONG' THEN 1 ELSE 0 END) as longs,
                    SUM(CASE WHEN signal='SHORT' THEN 1 ELSE 0 END) as shorts
                FROM signals
                WHERE scan_date >= date('now', ?)
                GROUP BY scan_date
                ORDER BY scan_date DESC
            """, (f"-{days} days",))
            rows = c.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                wins = d.get('wins', 0) or 0
                losses = d.get('losses', 0) or 0
                total_closed = wins + losses
                d['win_rate'] = round(wins / total_closed * 100, 1) if total_closed > 0 else 0
                result.append(d)
            return result
        except Exception as e:
            logger.error(f"[DB] get_daily_performance error: {e}")
            return []

    def close(self):
        self.conn.close()
