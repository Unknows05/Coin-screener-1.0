# Coin Screener Pro

Deep screening system for Binance USDT-M Futures dengan **Bayesian Learning Engine**, **3-Session Filter**, dan **Market Microstructure**. No API key needed.

## Fitur Utama

### Bayesian Learning Engine
- **Beta-Binomial Conjugate** — Belajar dari setiap WIN/LOSS secara real-time
- **Dynamic WR** — Angka WR diambil dari database, bukan hardcoded
- **Position Sizing** — Otomatis turun untuk combo low-WR, naik untuk high-WR
- **Market Reversal Detection** — Kalau market balik, system ikut adapt, tidak buta
- Contoh: SIDEWAYS+LONG WR 35% → position 63%. Kalau WR naik ke 45% → position otomatis naik ke 78%

### 3-Session Filter
- **ASIA** (00-08 UTC), **LONDON** (08-16 UTC), **NEW YORK** (13-22 UTC)
- **LONDON/NY Overlap** (13-16 UTC) — highest liquidity detection
- Perpetual always generates signals — sessions adjust SL width & score, tidak block
- Session WR from database adjusts confidence dynamically

### Adaptive Regime-Signal Handling
- **BEFORE**: SIDEWAYS+LONG = hard blocked (0 signals)
- **AFTER**: SIDEWAYS+LONG = allowed, position reduced, score penalty
- System NEVER goes blind — market reversal = system adapts

### ATR-Based Dynamic SL/TP
- Per-regime per-direction multipliers:
  - SIDEWAYS+SHORT: SL 1.5x ATR, TP 2.5x ATR (tight for mean-reversion)
  - BULL+LONG: SL 2.5x ATR, TP 3.5x ATR (wide for trends)
  - HIGH_VOL: SL 3.0x ATR, TP 4.0x ATR (widest for volatility)
- Minimum SL distance: 0.5% of price (prevents tight SL on low-ATR coins)

### Market Microstructure V2
- Real liquidation data (forceOrders)
- Whale position tracking (size-weighted)
- Order book wall detection
- Regime flip detection (minutes, not hours)

### Confidence Calibration
- Historical: confidence 90+ = 47.8% WR (inverted!)
- Now: confidence 90+ capped to 76, sweet spot 60-70 preserved
- Prevents overconfidence at score extremes

---

## Quick Start

### Install & Run

```bash
# Clone
git clone https://github.com/Unknows05/Coin-screener-1.0.git
cd Coin-screener-1.0

# Install dependencies
pip install -r requirements.txt

# Start server
python3 api.py

# Or use run.sh
./run.sh start

# Access dashboard
http://localhost:8000
```

### Production (Auto-start on boot, auto-restart on crash)

```bash
# One-time setup
sudo cp coin-screener.service /etc/systemd/system/
sudo systemctl enable coin-screener
sudo systemctl start coin-screener

# Check status
sudo systemctl status coin-screener

# View logs
sudo journalctl -u coin-screener -f
```

Service berjalan 24/7, auto-restart 10 detik setelah crash, auto-start saat OS boot.

---

## API Endpoints

### Core
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/api/status` | System status, current session |
| GET | `/api/scan/latest` | Latest scan result |
| POST | `/api/scan` | Trigger manual scan |

### Learning & Analytics (NEW)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/learning` | **Bayesian beliefs** — what the system has learned |
| GET | `/api/feedback` | **Dynamic WR** — real win rates from database |
| GET | `/api/session` | **Session context** — current trading session + reversal detection |
| GET | `/api/rl/performance?days=7` | RL performance per regime |
| GET | `/api/rl/report` | Human-readable analysis |

**Contoh `/api/learning` response:**
```json
{
  "ok": true,
  "beliefs": {
    "SIDEWAYS+LONG": {"wr_estimate": 35.2, "confidence": 100, "total_trades": 693, "learning_source": "bayesian"},
    "SIDEWAYS+SHORT": {"wr_estimate": 62.6, "confidence": 100, "total_trades": 3050, "learning_source": "bayesian"},
    "BULL+SHORT": {"wr_estimate": 66.3, "confidence": 100, "total_trades": 368, "learning_source": "bayesian"}
  },
  "explanation": {
    "framework": "Bayesian Beta-Binomial Conjugate",
    "how_it_works": [
      "1. Prior: Beta(2,2) = uniform belief (50% WR)",
      "2. Each WIN: alpha += 1 (more evidence for high WR)",
      "3. Each LOSS: beta += 1 (more evidence for low WR)",
      "4. Posterior: Beta(alpha, beta) = learned WR estimate",
      "5. After 30+ trades: converges to true WR"
    ]
  }
}
```

### Signals & History
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/signals` | Active LONG/SHORT signals |
| GET | `/api/signals/history?limit=50` | History with SL/TP outcomes |
| GET | `/api/db/stats` | Database statistics |
| GET | `/api/calendar/{year}/{month}` | Calendar with chart data |

---

## Usage Examples

```bash
# Health check
curl http://localhost:8000/health

# What has the system LEARNED?
curl http://localhost:8000/api/learning | jq '.beliefs'

# Dynamic WR from real data
curl http://localhost:8000/api/feedback | jq '.regime_signal'

# Current session context (Asia/London/NY)
curl http://localhost:8000/api/session | jq '.session, .reversal'

# Active signals
curl http://localhost:8000/api/signals | jq '.data[] | {symbol, signal, regime, session, position_reduction}'

# Signal history with outcomes
curl http://localhost:8000/api/signals/history?limit=20 | jq '.data[] | {symbol, signal, result, regime}'
```

---

## Architecture

```
 SCAN CYCLE (every 15 min)
 ┌─────────────────────────────────────────────────────────────────┐
 │  1. Fetch Data  →  Binance API (klines, tickers)              │
 │  2. Regime V2    →  Price + Microstructure detection          │
 │  3. Score        →  Multi-TF + RL adaptive weights             │
 │  4. Signal       →  LONG/SHORT/WAIT + ATR-based SL/TP        │
 │  5. Session      →  Adjust score/SL by session WR/volatility   │
 │  6. Reversal     →  If market shifting, boost opposing bias    │
 │  7. Risk Check   →  Bayesian beliefs → position size + penalty  │
 │  8. Save & Learn →  DB ← Outcome ← Bayesian update           │
 └─────────────────────────────────────────────────────────────────┘

 LEARNING PIPELINE (continuous)
 ┌─────────────────────────────────────────────────────────────────┐
 │  outcome_feedback.py  →  REAL WR from last 7 days (not static)│
 │  learning_engine.py   →  Beta-Binomial beliefs (auto-update)  │
 │  adaptive_rl.py       →  Factor weight optimization per regime │
 │                                                                │
 │  NEVER HARDCODED:                                               │
 │  - SIDEWAYS+LONG was 35.2% → now 31.6% from 402 trades         │
 │  - BULL+LONG was 58.7% → now 22.6% from 296 trades            │
 │  - Numbers update every scan from REAL data                     │
 └─────────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
coin-screener/
├── api.py                      # FastAPI server + scheduler
├── config.yaml                 # All configuration
├── requirements.txt            # Python dependencies
├── run.sh                      # Start/stop/status/logs
├── static/
│   ├── dashboard.html          # V1 dashboard
│   └── dashboard_new.html      # V2 dashboard (Win Rate Analytics)
├── src/
│   ├── learning_engine.py      # Bayesian Beta-Binomial learning
│   ├── outcome_feedback.py     # Dynamic WR from database
│   ├── session_filter.py       # 3-session adaptive filter
│   ├── engine_v2.py            # Screening engine V2
│   ├── risk_manager.py         # Risk manager (adaptive, not blocking)
│   ├── risk_manager_v2.py      # V2 risk with microstructure
│   ├── signals.py              # ATR-based signal generation
│   ├── scorer.py               # Multi-TF scoring
│   ├── regime.py / regime_v2.py# Regime detection
│   ├── adaptive_rl.py          # RL weight optimizer
│   ├── enhanced_data_v2.py     # Liquidation, whale, order book
│   ├── binance_api.py          # Binance REST (fast-fail on 401)
│   ├── database.py             # SQLite signal tracking
│   └── ...
└── data/
    ├── screener.db              # SQLite (5700+ signals with outcomes)
    ├── adaptive_weights.json   # RL weights (auto-updated)
    ├── learning_state.json      # Bayesian beliefs (auto-updated)
    ├── outcome_feedback.json   # Dynamic WR report
    └── last_scan.json          # Cached scan result
```

---

## Configuration

Edit `config.yaml`:

```yaml
# Trading Sessions (UTC)
sessions:
  enabled: true
  asia:
    start_utc: 0
    end_utc: 8
  london:
    start_utc: 8
    end_utc: 16
  new_york:
    start_utc: 13
    end_utc: 22

# SL/TP per regime + direction (ATR multipliers)
# SIDEWAYS+SHORT: SL=1.5x, TP=2.5x (tight for mean-reversion)
# BULL+LONG: SL=2.5x, TP=3.5x (wide for trends)
# HIGH_VOL: SL=3.0x, TP=4.0x (widest)
risk:
  sl_atr_multiplier: 1.5
  tp_atr_multiplier: 3.0

# Adaptive learning
adaptive:
  decay_factor: 0.95
  session_feedback: true
  reversal_detection: true

# Signal thresholds
signal:
  long_min_score: 55
  short_min_score: 55

# Microstructure V2
microstructure:
  enabled: true
  enhanced_symbols: null  # null = all coins
```

---

## Learning System Detail

### How It Actually Learns

```
BEFORE (deprecated):
  low_wr_combos = {
      ("SIDEWAYS", "LONG"): 35.2,    # HARDCODED, never changes
      ("BEAR", "SHORT"): 44.8,       # Market shifts? System stays blind
  }

AFTER (current):
  Every scan cycle:
  1. save_signals()      → Store signal to DB
  2. check_outcomes()     → Check if TP/SL hit → WIN/LOSS
  3. outcome_feedback()   → Calculate REAL WR from last 7 days
  4. learning_engine()    → Bayesian update: Beta(alpha+wins, beta+losses)
  5. risk_manager()       → Use LEARNED beliefs for position sizing
  6. adaptive_rl()        → Optimize factor weights per regime
  
  Result: if SIDEWAYS+LONG WR improves from 35% → 45%,
          position size AUTOMATICALLY increases from 63% → 78%.
          No manual update needed.
```

### Bayesian Beta-Binomial Math
```
Prior (no data):   Beta(2, 2) → WR estimate = 50% (uncertain)
After 10 trades:   Beta(2+7, 2+3) → WR = 70% (low confidence)
After 300 trades:  Beta(2+189, 2+111) → WR = 62.6% (high confidence)
After 3000 trades: Beta(2+1909, 2+1141) → WR = 62.6% (converged)

Decision:
  WR < 38% → position = WR/50 (small, e.g. 31.6% → 63%)
  WR > 60% → position = 0.7 + (WR-60)*3 (boosted, e.g. 66.3% → 90%)
  WR 38-60% → normal with slight adjustment
```

### Market Reversal Detection
```
Last 3 days vs Last 14 days:
  If SHORT WR dropped -41% and LONG WR rose +50%
  → bias_shift = +10 (boost LONG signals)
  → System follows the reversal, doesn't stay blind
```

---

## Dashboard

### V2 Dashboard (Win Rate Analytics)
- **Summary Cards**: Overall WR, Active Signals, Regime WR, V2 Blocks
- **Session Indicator**: Current session (ASIA/LONDON/NY) with color
- **Win Rate Analysis**: LONG/SHORT WR, Regime WR, Blocked Signals
- **Daily Calendar**: Win rate per day with color coding
- **Signals Table**: Symbol, Signal, Session, Risk, Position Reduction, Entry/SL/TP
- **Signal History**: Filterable (All/Wins/Losses)
- **Microstructure**: Liquidations, Whale Activity, Order Book

### Adaptive Learning Insights
- Only shown when data is available (auto-hidden when empty)

---

## Management Commands

```bash
# Using run.sh
./run.sh start       # Start server
./run.sh stop        # Stop server  
./run.sh status      # Health check
./run.sh logs        # Follow logs
./run.sh restart     # Restart

# Using systemctl (production)
sudo systemctl start coin-screener
sudo systemctl stop coin-screener
sudo systemctl status coin-screener
sudo journalctl -u coin-screener -f
```

---

## Troubleshooting

```bash
# Port 8000 in use
lsof -ti:8000 | xargs kill -9

# Database locked
rm -f data/screener.db-shm data/screener.db-wal

# Check what system has learned
curl http://localhost:8000/api/learning | jq '.beliefs'

# Check dynamic WR
curl http://localhost:8000/api/feedback | jq '.regime_signal'

# Check server health
curl http://localhost:8000/health

# Restart service
sudo systemctl restart coin-screener
```

---

## Requirements

- **Python**: 3.10+
- **RAM**: 512MB minimum, 1GB recommended
- **Storage**: 50MB (database grows ~5MB/week)
- **Network**: Stable internet for Binance API
- **OS**: Linux recommended (systemd auto-start)

**Dependencies:**
```
requests>=2.31.0
pandas>=2.1.0
numpy>=1.26.0
pyyaml>=6.0
fastapi>=0.109.0
uvicorn>=0.27.0
apscheduler>=3.10.4
```

---

## License

MIT License