"""
Online Learning Engine — True continuous learning from trade outcomes.

Framework: EMA-based Bayesian Update (no external packages needed)

How it ACTUALLY learns:
1. After each scan: new signals saved, outcomes checked
2. outcome_feedback.py: calculates REAL WR per regime+signal from DB
3. This module: Bayesian update of belief distribution
4. Weights, thresholds, and position sizes ALL update from data

Key difference from before:
- OLD: hardcoded numbers (35.2%, 44.8%) NEVER change
- NEW: posterior estimates update AFTER every trade cycle

Math:
  prior = Beta(alpha, beta)  ← our belief about WR
  likelihood = trade outcome   ← WIN=1, LOSS=0
  posterior = Beta(alpha + wins, beta + losses)  ← updated belief
  
  This is CONJUGATE — means we can update analytically, no sampling needed.
  Cold start: use historical data as prior (Beta(2,2) = uniform).
  After 30+ trades: posterior converges to true WR.

Decision rule:
  - WR < 38%: position = WR/50 (small size)
  - WR 38-50%: position = WR/65 (reduced)
  - WR 50-60%: position = WR/70 (normal)
  - WR > 60%: position = min(1.0, 0.7 + (WR-60)*0.03) (boosted)
  
  Score penalty/bonus:
  - WR < 38%: penalty = (50-WR)*0.15 (max -12)
  - WR 38-50%: penalty = (50-WR)*0.07 (max -5)
  - WR > 60%: bonus = -(WR-55)*0.3 (max -5, negative = boost)
"""
import json
import logging
import sqlite3
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class RegimeBelief:
    regime: str
    signal: str
    alpha: float = 2.0
    beta: float = 2.0
    last_updated: str = ""
    
    @property
    def wr_estimate(self) -> float:
        total = self.alpha + self.beta
        if total <= 4:
            return 0.5
        return (self.alpha - 2) / (total - 4)
    
    @property
    def confidence(self) -> float:
        total = self.alpha + self.beta - 4
        if total <= 0:
            return 0.0
        return min(1.0, total / 50.0)
    
    @property
    def total_trades(self) -> int:
        return int(self.alpha + self.beta - 4)
    
    def to_dict(self) -> dict:
        return {
            "regime": self.regime,
            "signal": self.signal,
            "alpha": self.alpha,
            "beta": self.beta,
            "wr_estimate": round(self.wr_estimate * 100, 1),
            "confidence": round(self.confidence * 100, 1),
            "total_trades": self.total_trades,
            "last_updated": self.last_updated,
        }


class OnlineLearningEngine:
    """
    Bayesian online learning engine for signal optimization.
    
    Uses Beta-Binomial conjugate prior for WR estimation.
    No external ML packages needed. Pure Python + numpy-free.
    """
    
    def __init__(self, db_path: str = "data/screener.db",
                 state_path: str = "data/learning_state.json"):
        self.db_path = db_path
        self.state_path = Path(state_path)
        self.beliefs: Dict[Tuple[str, str], RegimeBelief] = {}
        self._last_update: Optional[datetime] = None
        self._cache_ttl_minutes = 15
        self._load_state()
        self._bootstrap_from_db()
    
    def _load_state(self):
        if self.state_path.exists():
            try:
                with open(self.state_path) as f:
                    data = json.load(f)
                for k, v in data.get("beliefs", {}).items():
                    regime, signal = k.split("+")
                    self.beliefs[(regime, signal)] = RegimeBelief(
                        regime=regime,
                        signal=signal,
                        alpha=v.get("alpha", 2.0),
                        beta=v.get("beta", 2.0),
                        last_updated=v.get("last_updated", ""),
                    )
                logger.info(f"[Learning] Loaded {len(self.beliefs)} beliefs from state")
            except Exception as e:
                logger.warning(f"[Learning] State load failed: {e}")
    
    def _save_state(self):
        try:
            data = {
                "last_updated": datetime.now().isoformat(),
                "beliefs": {
                    f"{k[0]}+{k[1]}": v.to_dict()
                    for k, v in self.beliefs.items()
                },
            }
            with open(self.state_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"[Learning] State save failed: {e}")
    
    def _bootstrap_from_db(self, days: int = 30):
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
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
            for regime, signal, result, cnt in rows:
                key = (regime, signal)
                if key not in combo_data:
                    combo_data[key] = {"wins": 0, "losses": 0}
                if result == "WIN":
                    combo_data[key]["wins"] += cnt
                else:
                    combo_data[key]["losses"] += cnt
            
            updated = 0
            for (regime, signal), data in combo_data.items():
                wins = data["wins"]
                losses = data["losses"]
                
                if (regime, signal) in self.beliefs:
                    belief = self.beliefs[(regime, signal)]
                    total_db = wins + losses
                    if total_db > belief.total_trades:
                        belief.alpha = 2.0 + wins
                        belief.beta = 2.0 + losses
                        belief.last_updated = datetime.now().isoformat()
                        updated += 1
                else:
                    self.beliefs[(regime, signal)] = RegimeBelief(
                        regime=regime,
                        signal=signal,
                        alpha=2.0 + wins,
                        beta=2.0 + losses,
                        last_updated=datetime.now().isoformat(),
                    )
                    updated += 1
            
            if updated > 0:
                self._save_state()
                logger.info(f"[Learning] Bootstrapped {updated} beliefs from DB")
        except Exception as e:
            logger.error(f"[Learning] Bootstrap failed: {e}")
    
    def update_from_trades(self, regime: str, signal: str, won: bool):
        key = (regime, signal)
        if key not in self.beliefs:
            self.beliefs[key] = RegimeBelief(regime=regime, signal=signal)
        
        belief = self.beliefs[key]
        if won:
            belief.alpha += 1
        else:
            belief.beta += 1
        belief.last_updated = datetime.now().isoformat()
        
        self._save_state()
    
    def get_wr_estimate(self, regime: str, signal: str) -> Tuple[float, float, int]:
        key = (regime, signal)
        if key in self.beliefs:
            belief = self.beliefs[key]
            return belief.wr_estimate, belief.confidence, belief.total_trades
        
        legacy_wr = {
            ("SIDEWAYS", "LONG"): 0.35, ("SIDEWAYS", "SHORT"): 0.63,
            ("BULL", "LONG"): 0.59, ("BULL", "SHORT"): 0.66,
            ("BEAR", "SHORT"): 0.45, ("BEAR", "LONG"): 0.50,
            ("HIGH_VOL", "LONG"): 0.71, ("HIGH_VOL", "SHORT"): 0.37,
        }
        wr = legacy_wr.get(key, 0.50)
        return wr, 0.0, 0
    
    def get_position_size(self, regime: str, signal: str) -> Tuple[float, str]:
        wr, confidence, trades = self.get_wr_estimate(regime, signal)
        
        if trades < 10:
            legacy_wr = {
                ("SIDEWAYS", "LONG"): 0.35, ("SIDEWAYS", "SHORT"): 0.63,
                ("BULL", "LONG"): 0.59, ("BULL", "SHORT"): 0.66,
                ("BEAR", "SHORT"): 0.45, ("BEAR", "LONG"): 0.50,
                ("HIGH_VOL", "LONG"): 0.71, ("HIGH_VOL", "SHORT"): 0.37,
            }
            fallback = legacy_wr.get((regime, signal), 0.50)
            pos = max(0.25, min(1.0, fallback / 0.65))
            return pos, f"legacy:{fallback:.0%}({trades}t)"
        
        if wr < 0.38:
            position = max(0.20, wr / 0.50)
        elif wr < 0.50:
            position = max(0.40, wr / 0.65)
        elif wr < 0.60:
            position = wr / 0.72
        else:
            position = min(1.0, 0.70 + (wr - 0.60) * 3.0)
        
        if confidence < 0.3:
            position *= 0.85
        
        source = f"learned:{wr:.1%}({trades}t,conf:{confidence:.0%})"
        return round(position, 2), source
    
    def get_confidence_adjustment(self, regime: str, signal: str) -> Tuple[int, str]:
        wr, confidence, trades = self.get_wr_estimate(regime, signal)
        
        if trades < 10:
            return 0, f"insufficient({trades}t)"
        
        if wr >= 0.60:
            bonus = min(5, int((wr - 0.55) * 30))
            return -bonus, f"high_wr:{wr:.1%}({trades}t)"
        elif wr < 0.38:
            penalty = min(12, int((0.50 - wr) * 24))
            return penalty, f"low_wr:{wr:.1%}({trades}t)"
        elif wr < 0.45:
            penalty = min(7, int((0.50 - wr) * 14))
            return penalty, f"below_avg:{wr:.1%}({trades}t)"
        
        return 0, f"neutral:{wr:.1%}({trades}t)"
    
    def get_adaptive_threshold(self, regime: str) -> int:
        base_thresholds = {
            "BULL": 50, "BEAR": 55, "SIDEWAYS": 55, "HIGH_VOL": 55, "DEFAULT": 55
        }
        base = base_thresholds.get(regime, 55)
        
        for signal in ("LONG", "SHORT"):
            wr, conf, trades = self.get_wr_estimate(regime, signal)
            if trades < 15:
                continue
            if wr > 0.60:
                base = max(45, base - 1)
            elif wr < 0.40:
                base = min(65, base + 2)
        
        return base
    
    def get_report(self) -> dict:
        report = {}
        for (regime, signal), belief in self.beliefs.items():
            key = f"{regime}+{signal}"
            report[key] = {
                "wr_estimate": round(belief.wr_estimate * 100, 1),
                "confidence": round(belief.confidence * 100, 1),
                "total_trades": belief.total_trades,
                "alpha": round(belief.alpha, 1),
                "beta": round(belief.beta, 1),
                "last_updated": belief.last_updated[:16] if belief.last_updated else "?",
                "learning_source": "bayesian" if belief.total_trades >= 10 else "prior+data",
            }
        return report


_learning_engine: Optional[OnlineLearningEngine] = None


def get_learning_engine(db_path: str = "data/screener.db") -> OnlineLearningEngine:
    global _learning_engine
    if _learning_engine is None:
        _learning_engine = OnlineLearningEngine(db_path)
    return _learning_engine