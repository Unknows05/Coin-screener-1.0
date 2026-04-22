"""
Session Filter — 3 Trading Sessions with Adaptive WR Context.

Sessions:
- ASIA: 00:00-08:00 UTC (Tokyo/Singapore)
- LONDON: 08:00-16:00 UTC (London/European)
- NEW_YORK: 13:00-22:00 UTC (NY/American overlap starts 13:00)

KEY DESIGN: Perpetual always generates signals. Sessions provide CONTEXT, not BLOCKS.
- Session WR is used to adjust confidence and score thresholds
- Not trading during "bad" sessions = opportunity loss
- Instead, adjust position size and SL/TP based on session characteristics
"""
import logging
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class SessionInfo:
    name: str
    session_type: str
    wr_modifier: float
    vol_modifier: float
    is_overlap: bool
    utc_hour: int


TRADING_SESSIONS = {
    "ASIA": {
        "start_utc": 0,
        "end_utc": 8,
        "typical_wr": 0.52,
        "vol_factor": 0.7,
        "description": "Lower vol, moderate WR"
    },
    "LONDON": {
        "start_utc": 8,
        "end_utc": 16,
        "typical_wr": 0.62,
        "vol_factor": 1.2,
        "description": "Higher vol, best WR"
    },
    "NEW_YORK": {
        "start_utc": 13,
        "end_utc": 22,
        "typical_wr": 0.58,
        "vol_factor": 1.0,
        "description": "Good vol, solid WR"
    },
}

SESSION_OVERLAP = {
    "LONDON_NY": {
        "start_utc": 13,
        "end_utc": 16,
        "wr_boost": 0.05,
        "vol_boost": 0.3,
        "description": "London-NY overlap, highest liquidity"
    },
}


class SessionFilter:
    def __init__(self, db_path: str = "data/screener.db"):
        self.db_path = db_path
        self._session_wr_cache: Dict[str, dict] = {}
        self._cache_time: Optional[datetime] = None
        self._cache_ttl_minutes = 30

    def get_current_session(self, utc_dt: datetime = None) -> SessionInfo:
        if utc_dt is None:
            utc_dt = datetime.now(timezone.utc)
        hour = utc_dt.hour

        overlap = self._get_overlap(hour)
        if overlap:
            return SessionInfo(
                name="LONDON_NY_OVERLAP",
                session_type="overlap",
                wr_modifier=1.05,
                vol_modifier=1.3,
                is_overlap=True,
                utc_hour=hour,
            )

        for name, cfg in TRADING_SESSIONS.items():
            if cfg["start_utc"] <= hour < cfg["end_utc"]:
                return SessionInfo(
                    name=name,
                    session_type=name.lower(),
                    wr_modifier=cfg["typical_wr"] / 0.55,
                    vol_modifier=cfg["vol_factor"],
                    is_overlap=False,
                    utc_hour=hour,
                )

        return SessionInfo(
            name="OFF_HOURS",
            session_type="off_hours",
            wr_modifier=0.9,
            vol_modifier=0.5,
            is_overlap=False,
            utc_hour=hour,
        )

    def _get_overlap(self, hour: int) -> Optional[dict]:
        for name, cfg in SESSION_OVERLAP.items():
            if cfg["start_utc"] <= hour < cfg["end_utc"]:
                return cfg
        return None

    def get_session_context(self, utc_dt: datetime = None) -> dict:
        session = self.get_current_session(utc_dt)
        session_wr = self._load_session_wr()

        current_wr = session_wr.get(session.name, {}).get("wr", 0.5)
        current_samples = session_wr.get(session.name, {}).get("samples", 0)

        wr_known = current_samples >= 10
        adaptive_modifier = 1.0
        if wr_known:
            adaptive_modifier = max(0.85, min(1.15, (current_wr - 0.5) * 3 + 1.0))

        return {
            "session_name": session.name,
            "session_type": session.session_type,
            "is_overlap": session.is_overlap,
            "wr_modifier": session.wr_modifier,
            "adaptive_modifier": adaptive_modifier,
            "vol_modifier": session.vol_modifier,
            "historic_wr": current_wr if wr_known else None,
            "historic_samples": current_samples,
            "utc_hour": session.utc_hour,
        }

    def apply_session_to_signal(
        self, signal_result: dict, session_context: dict
    ) -> dict:
        score = signal_result.get("score", 50)
        signal_type = signal_result.get("signal", "WAIT")

        sm = session_context.get("adaptive_modifier", 1.0)
        vm = session_context.get("vol_modifier", 1.0)

        adjusted_score = score
        if signal_type == "LONG":
            adjusted_score = score * sm
        elif signal_type == "SHORT":
            adjusted_score = score * sm

        adjusted_score = max(0, min(100, adjusted_score))

        vol_mult = 1.0 / max(0.3, vm)
        sl = signal_result.get("sl")
        tp = signal_result.get("tp")
        entry = signal_result.get("entry", signal_result.get("price", 0))

        if sl and entry:
            sl_dist = abs(entry - sl)
            adjusted_sl_dist = sl_dist * vol_mult
            if signal_type == "LONG":
                sl = round(entry - adjusted_sl_dist, 6)
            elif signal_type == "SHORT":
                sl = round(entry + adjusted_sl_dist, 6)

        if tp and entry:
            tp_dist = abs(tp - entry)
            session_tp_mult = 1.0 + (1.0 - sm) * 0.5
            adjusted_tp_dist = tp_dist * session_tp_mult
            if signal_type == "LONG":
                tp = round(entry + adjusted_tp_dist, 6)
            elif signal_type == "SHORT":
                tp = round(entry - adjusted_tp_dist, 6)

        signal_result["score"] = round(adjusted_score, 1)
        signal_result["sl"] = sl
        signal_result["tp"] = tp
        signal_result["session"] = session_context.get("session_name", "UNKNOWN")
        signal_result["session_context"] = {
            "name": session_context.get("session_name"),
            "wr_modifier": round(sm, 3),
            "vol_modifier": round(vm, 3),
            "historic_wr": session_context.get("historic_wr"),
        }

        return signal_result

    def _load_session_wr(self) -> dict:
        now = datetime.now(timezone.utc)
        if (
            self._cache_time
            and (now - self._cache_time).total_seconds() < self._cache_ttl_minutes * 60
        ):
            return self._session_wr_cache

        try:
            from src.database import ScreenerDB

            db = ScreenerDB(self.db_path)
            signals = db.get_signals_with_outcomes(limit=1000)
            db.close()

            session_data = {}
            for s in signals:
                if s.get("result") not in ("WIN", "LOSS"):
                    continue
                ts = s.get("timestamp", "")
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    hour = dt.hour
                except Exception:
                    continue

                session_name = self._hour_to_session(hour)
                if session_name not in session_data:
                    session_data[session_name] = {"wins": 0, "losses": 0}
                if s.get("result") == "WIN":
                    session_data[session_name]["wins"] += 1
                else:
                    session_data[session_name]["losses"] += 1

            result = {}
            for name, data in session_data.items():
                total = data["wins"] + data["losses"]
                if total > 0:
                    result[name] = {
                        "wr": data["wins"] / total,
                        "samples": total,
                    }
                else:
                    result[name] = {"wr": 0.5, "samples": 0}

            self._session_wr_cache = result
            self._cache_time = now
            return result

        except Exception as e:
            logger.debug(f"[SessionFilter] WR load failed: {e}")
            return {}

    def _hour_to_session(self, hour: int) -> str:
        for name, cfg in TRADING_SESSIONS.items():
            if cfg["start_utc"] <= hour < cfg["end_utc"]:
                return name
        if 13 <= hour < 16:
            return "LONDON_NY_OVERLAP"
        if hour >= 22 or hour < 0:
            return "OFF_HOURS"
        return "UNKNOWN"


_session_filter: Optional[SessionFilter] = None


def get_session_filter(db_path: str = "data/screener.db") -> SessionFilter:
    global _session_filter
    if _session_filter is None:
        _session_filter = SessionFilter(db_path)
    return _session_filter