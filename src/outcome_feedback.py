"""
Outcome Feedback Engine — Makes the system truly learn from trade results.

The KEY problem it solves:
- Data was being saved (5728 closed trades) but NOT used to update decision logic
- WR numbers were hardcoded (35.2%, 44.8%, etc) and NEVER updated
- Score thresholds were STATIC regardless of actual performance

This module:
1. Loads REAL win rates from DB (last 7/30 days, not all-time)
2. Updates low/high WR combos dynamically
3. Adjusts score thresholds based on actual performance
4. Tracks regime+direction+session performance over time
5. Computes per-coin WR to detect coin-specific edges
"""
import logging
import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ComboWR:
    win_rate: float
    total: int
    wins: int
    is_stale: bool = False


class OutcomeFeedback:
    def __init__(self, db_path: str = "data/screener.db",
                 cache_path: str = "data/outcome_feedback.json"):
        self.db_path = db_path
        self.cache_path = Path(cache_path)
        self._cache: Dict = {}
        self._cache_time: Optional[datetime] = None
        self._cache_ttl_minutes = 15

    def load_regime_wr(self, days: int = 7) -> Dict[Tuple[str, str], ComboWR]:
        results = {}
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            since = (datetime.now() - timedelta(days=days)).isoformat()
            c.execute("""
                SELECT regime, signal, result, COUNT(*) as cnt
                FROM signals
                WHERE timestamp > ? AND result IN ('WIN', 'LOSS')
                GROUP BY regime, signal, result
            """, (since,))
            rows = c.fetchall()
            conn.close()

            combo_data = {}
            for r in rows:
                key = (r["regime"], r["signal"])
                if key not in combo_data:
                    combo_data[key] = {"wins": 0, "total": 0}
                combo_data[key]["total"] += r["cnt"]
                if r["result"] == "WIN":
                    combo_data[key]["wins"] += r["cnt"]

            for (regime, signal), data in combo_data.items():
                total = data["total"]
                wins = data["wins"]
                wr = (wins / total * 100) if total > 0 else 50.0
                results[(regime, signal)] = ComboWR(
                    win_rate=wr,
                    total=total,
                    wins=wins,
                    is_stale=total < 10
                )
        except Exception as e:
            logger.error(f"[Feedback] Failed to load regime WR: {e}")
        return results

    def load_session_wr(self, days: int = 14) -> Dict[str, ComboWR]:
        results = {}
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            since = (datetime.now() - timedelta(days=days)).isoformat()
            c.execute("""
                SELECT timestamp, result, COUNT(*) as cnt
                FROM signals
                WHERE timestamp > ? AND result IN ('WIN', 'LOSS')
                GROUP BY timestamp, result
            """, (since,))
            rows = c.fetchall()
            conn.close()

            from src.session_filter import SessionFilter
            sf = SessionFilter(self.db_path)
            session_data = {}
            for r in rows:
                ts = r["timestamp"]
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    hour = dt.hour
                except Exception:
                    continue
                session_name = sf._hour_to_session(hour)
                if session_name not in session_data:
                    session_data[session_name] = {"wins": 0, "total": 0}
                session_data[session_name]["total"] += r["cnt"]
                if r["result"] == "WIN":
                    session_data[session_name]["wins"] += r["cnt"]

            for session, data in session_data.items():
                total = data["total"]
                wins = data["wins"]
                wr = (wins / total * 100) if total > 0 else 50.0
                results[session] = ComboWR(win_rate=wr, total=total, wins=wins)
        except Exception as e:
            logger.error(f"[Feedback] Failed to load session WR: {e}")
        return results

    def load_coin_wr(self, days: int = 14, min_trades: int = 10
                    ) -> Dict[str, ComboWR]:
        results = {}
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            since = (datetime.now() - timedelta(days=days)).isoformat()
            c.execute("""
                SELECT symbol, result, COUNT(*) as cnt
                FROM signals
                WHERE timestamp > ? AND result IN ('WIN', 'LOSS')
                GROUP BY symbol, result
            """, (since,))
            rows = c.fetchall()
            conn.close()

            coin_data = {}
            for r in rows:
                sym = r["symbol"]
                if sym not in coin_data:
                    coin_data[sym] = {"wins": 0, "total": 0}
                coin_data[sym]["total"] += r["cnt"]
                if r["result"] == "WIN":
                    coin_data[sym]["wins"] += r["cnt"]

            for sym, data in coin_data.items():
                total = data["total"]
                wins = data["wins"]
                wr = (wins / total * 100) if total > 0 else 50.0
                if total >= min_trades:
                    results[sym] = ComboWR(win_rate=wr, total=total, wins=wins)
        except Exception as e:
            logger.error(f"[Feedback] Failed to load coin WR: {e}")
        return results

    def get_dynamic_regime_signal_wr(self) -> Dict[Tuple[str, str], float]:
        combo_wr = self.load_regime_wr(days=7)
        result = {}
        for (regime, signal), cwr in combo_wr.items():
            result[(regime, signal)] = cwr.win_rate / 100.0
        return result

    def get_adaptive_thresholds(self) -> Dict[str, float]:
        combo_wr = self.load_regime_wr(days=7)
        thresholds = {
            "BULL": 50, "BEAR": 55, "SIDEWAYS": 55, "HIGH_VOL": 55, "DEFAULT": 55
        }
        for (regime, signal), cwr in combo_wr.items():
            if cwr.total < 20:
                continue
            wr = cwr.win_rate
            if signal == "LONG" and wr > 60:
                current = thresholds.get(regime, 55)
                thresholds[regime] = max(45, current - 2)
            elif signal == "LONG" and wr < 45:
                current = thresholds.get(regime, 55)
                thresholds[regime] = min(65, current + 3)
            elif signal == "SHORT" and wr > 60:
                current = thresholds.get(regime, 55)
                thresholds[regime] = max(45, current - 2)
        return thresholds

    def get_position_reduction(self, regime: str, signal: str) -> Tuple[float, str]:
        combo_wr = self.load_regime_wr(days=7)
        key = (regime, signal)
        cwr = combo_wr.get(key)
        if cwr is None or cwr.is_stale:
            fallback_wr = {
                ("SIDEWAYS", "LONG"): 0.352, ("BEAR", "SHORT"): 0.448,
                ("BULL", "SHORT"): 0.663, ("BULL", "LONG"): 0.587,
                ("SIDEWAYS", "SHORT"): 0.626, ("HIGH_VOL", "SHORT"): 0.368,
                ("HIGH_VOL", "LONG"): 0.708, ("BEAR", "LONG"): 0.50,
            }
            wr = fallback_wr.get(key, 0.50)
            position = max(0.25, wr / 0.65)
            return position, f"static:{wr:.1%}"
        wr = cwr.win_rate / 100.0
        if wr < 0.40:
            position = max(0.20, wr / 0.50)
        elif wr < 0.50:
            position = max(0.40, wr / 0.65)
        elif wr < 0.60:
            position = wr / 0.70
        else:
            position = min(1.0, 0.7 + (wr - 0.60) * 3.0)
        source = f"dynamic:{cwr.win_rate:.1f}%({cwr.total})"
        return round(position, 2), source

    def get_confidence_penalty(self, regime: str, signal: str) -> Tuple[int, str]:
        combo_wr = self.load_regime_wr(days=7)
        key = (regime, signal)
        cwr = combo_wr.get(key)
        if cwr is None or cwr.is_stale:
            return 0, "insufficient_data"
        wr = cwr.win_rate
        if wr >= 60:
            bonus = min(5, int((wr - 55) * 0.5))
            return -bonus, f"high_wr:{wr:.1f}%"
        elif wr < 38:
            penalty = min(12, int((50 - wr) * 0.5))
            return penalty, f"low_wr:{wr:.1f}%"
        elif wr < 45:
            penalty = min(7, int((50 - wr) * 0.3))
            return penalty, f"below_avg:{wr:.1f}%"
        return 0, f"avg:{wr:.1f}%"

    def save_feedback_report(self):
        combo_wr = self.load_regime_wr(days=7)
        session_wr = self.load_session_wr(days=14)
        coin_wr = self.load_coin_wr(days=14, min_trades=10)
        report = {
            "timestamp": datetime.now().isoformat(),
            "regime_signal_wr": {
                f"{k[0]}_{k[1]}": {
                    "wr": round(v.win_rate, 1),
                    "total": v.total,
                    "wins": v.wins,
                    "stale": v.is_stale
                }
                for k, v in combo_wr.items()
            },
            "session_wr": {
                k: {"wr": round(v.win_rate, 1), "total": v.total, "wins": v.wins}
                for k, v in session_wr.items()
            },
            "coin_wr": {
                k: {"wr": round(v.win_rate, 1), "total": v.total, "wins": v.wins}
                for k, v in coin_wr.items()
            },
            "adaptive_thresholds": self.get_adaptive_thresholds(),
        }
        try:
            with open(self.cache_path, "w") as f:
                json.dump(report, f, indent=2)
            logger.info(f"[Feedback] Saved report to {self.cache_path}")
        except Exception as e:
            logger.error(f"[Feedback] Save failed: {e}")

    def get_report(self) -> dict:
        combo_wr = self.load_regime_wr(days=7)
        session_wr = self.load_session_wr(days=14)
        return {
            "regime_signal": {
                f"{k[0]}+{k[1]}": {
                    "wr": round(v.win_rate, 1),
                    "trades": v.total
                }
                for k, v in combo_wr.items()
            },
            "session": {
                k: {"wr": round(v.win_rate, 1), "trades": v.total}
                for k, v in session_wr.items()
            },
        }


_feedback_instance: Optional[OutcomeFeedback] = None


def get_feedback(db_path: str = "data/screener.db") -> OutcomeFeedback:
    global _feedback_instance
    if _feedback_instance is None:
        _feedback_instance = OutcomeFeedback(db_path)
    return _feedback_instance