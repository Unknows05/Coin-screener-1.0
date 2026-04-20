# 📊 Coin Screener Pro

Deep screening system for Binance USDT-M Futures dengan **Adaptive Reinforcement Learning** dan **Advanced Market Microstructure Analysis**. **No API key needed.**

## 🎯 Fitur Utama

### 🤖 Adaptive RL System (NEW!)
- **Reinforcement Learning** — Belajar dari setiap WIN/LOSS untuk optimasi sinyal
- **Kelly Criterion** — Position sizing optimal berdasarkan performance (e.g., 21.18% untuk SIDEWAYS)
- **Expectancy Tracking** — Edge calculation per regime (HIGH_VOL: 2.218 expectancy!)
- **Auto-Adjusting Weights** — Factor weights berubah setiap 15 menit berdasarkan hasil trading

### 📈 Enhanced Market Data (NEW!)
- **Long/Short Ratio** — Retail sentiment analysis (contrarian indicator)
- **Taker Buy/Sell Volume** — Order flow direction (aggressive vs passive)
- **Order Book Depth** — Liquidity walls & support/resistance clusters
- **Funding Rate History** — Carry cost analysis (extreme funding = reversal signal)
- **Open Interest Trends** — Positioning analysis (OI + Price divergence)
- **Top Trader Ratios** — Whale positioning tracking

### 🔍 Deep Screening
- **30 Alpha Factors** — Mean reversion, momentum, volume, volatility, pattern
- **Multi-Timeframe** — 15m (60%) + 1h (30%) + 4h (10%) weighted scoring
- **4-Layer Scoring** — Technical → Enhanced → Multi-TF → RL + Sentiment
- **Pattern Recognition** — Triangle, Flag, Double Top/Bottom, Breakout

### 📊 Signal Management
- **Signal Generation** — LONG/SHORT/WAIT dengan confidence 0-100%
- **SL/TP Auto-Tracking** — Automatic outcome detection setiap 15 menit
- **Win Rate by Regime** — Performance tracking: SIDEWAYS 62%, HIGH_VOL 54%
- **Outcome Database** — 5,685+ signals tracked dengan SL/TP results

### 📱 Dashboard Interface
- **Real-time Charts** — Cumulative PNL & Win Rate trends
- **Interactive Calendar** — Click untuk filter signals per tanggal
- **RL Insights Panel** — Adaptive learning metrics & recommendations
- **Signal History** — Modern table dengan filter (All/TP Hit/SL Hit/Open)

---

## 🚀 Quick Start

### Local Development (Mac/Linux)
```bash
# Clone repository
git clone https://github.com/Unknows05/Coin-screener-1.0.git
cd Coin-screener-1.0

# Setup virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # Mac/Linux

# Install dependencies
pip install -r requirements.txt

# Start server
./run.sh start

# Access dashboard
open http://localhost:8000  # Mac
# atau
xdg-open http://localhost:8000  # Linux
```

### VPS/Cloud Deployment
Lihat [DEPLOYMENT.md](DEPLOYMENT.md) untuk panduan lengkap deploy ke:
- ☁️ AWS EC2, DigitalOcean, Linode, Vultr
- 🐳 Docker & Docker Compose
- 🔧 Systemd service setup
- 🔒 Nginx reverse proxy

---

## 📡 API Endpoints

### Core Endpoints
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check & server status |
| GET | `/docs` | Swagger UI (auto-generated docs) |
| GET | `/api/status` | System status & scan metadata |

### Scanning Endpoints
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/scan` | Trigger screening now |
| GET | `/api/scan/latest` | Get latest scan result |

### Signal Endpoints
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/signals` | Only LONG/SHORT signals |
| GET | `/api/signals/history` | Full history dengan SL/TP outcomes |
| GET | `/api/alerts` | Active signals untuk alerts page |
| GET | `/api/coin/{SYMBOL}` | Detail untuk specific coin |

### Analytics Endpoints
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/db/stats` | Database statistics (wins/losses/win_rate) |
| GET | `/api/calendar/{year}/{month}` | Calendar view dengan chart data |
| GET | `/api/liquidations` | Liquidation heatmap data |

### 🆕 RL Endpoints (NEW!)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/rl/performance?days=7` | **Adaptive RL metrics** per regime |
| GET | `/api/rl/report` | **Human-readable analysis** report |

**Contoh Response RL Performance:**
```json
{
  "ok": true,
  "days_analyzed": 7,
  "performance": {
    "SIDEWAYS": {
      "total_trades": 2912,
      "wins": 1804,
      "losses": 1108,
      "win_rate": 61.95,
      "expectancy": 0.905,
      "kelly_fraction": 0.2071,
      "long_wr": 29.09,
      "short_wr": 63.92
    },
    "HIGH_VOL": {
      "total_trades": 44,
      "win_rate": 54.55,
      "expectancy": 2.218,
      "kelly_fraction": 0.1158
    }
  },
  "recommendations": {
    "SIDEWAYS": {
      "factor_weights": {
        "mean_reversion": 0.26,
        "momentum": 0.286,
        "volume": 0.2,
        "volatility": 0.15,
        "pattern": 0.104
      },
      "score_threshold": 60,
      "kelly_fraction": 0.2071
    }
  }
}
```

---

## 💡 Usage Examples

```bash
# Health check
curl http://localhost:8000/health

# Get RL performance (last 7 days)
curl http://localhost:8000/api/rl/performance?days=7 | jq

# Get RL human-readable report
curl http://localhost:8000/api/rl/report

# Get active signals
curl http://localhost:8000/api/signals | jq '.data[] | {symbol, signal, confidence}'

# Get signal history dengan outcomes
curl http://localhost:8000/api/signals/history?limit=50 | jq '.data[] | {symbol, result, pnl_pct}'

# Trigger manual scan
curl -X POST http://localhost:8000/api/scan

# Get calendar data
curl http://localhost:8000/api/calendar/2026/4 | jq '.chart_data'
```

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  FASTAPI SERVER (localhost:8000)                           │
│  ├─ APScheduler → Scan every 15 min                         │
│  ├─ Enhanced Data Fetcher → L/S, Funding, OI               │
│  ├─ RL Optimizer → Adaptive weight updates                 │
│  └─ Auto-restart via systemd (production)                  │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  SCREENING ENGINE                                           │
│  ├─ Layer 1: Technical (RSI, MACD, BB, Volume)             │
│  ├─ Layer 2: Enhanced (L/S Ratio, Funding, Order Book)     │
│  ├─ Layer 3: Multi-Timeframe (15m/1h/4h weighted)          │
│  ├─ Layer 4: RL + Sentiment (Adaptive adjustment)          │
│  └─ Signal Generator (LONG/SHORT/WAIT + SL/TP)              │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  OUTCOME TRACKING & RL LEARNING                             │
│  ├─ Check SL/TP hits setiap scan                            │
│  ├─ Update: WIN/LOSS/OPEN                                   │
│  ├─ Analyze: Performance per regime                         │
│  ├─ Adjust: Factor weights (mean_reversion, momentum, etc.) │
│  └─ Save: adaptive_weights.json                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 📁 Project Structure

```
coin-screener/
├── api.py                      # FastAPI server + scheduler
├── config.yaml                 # Configuration (symbols, thresholds)
├── requirements.txt            # Python dependencies
├── run.sh                      # Start/stop/status/logs manager
├── screen_once.py             # CLI single scan
├── DEPLOYMENT.md              # 🆕 VPS/Cloud deployment guide
├── static/
│   └── dashboard.html         # Web UI (Dashboard, History, RL Panel)
├── src/
│   ├── adaptive_rl.py         # 🆕 RL System (Kelly, Expectancy)
│   ├── enhanced_data.py       # 🆕 Market Microstructure Data
│   ├── engine.py              # Screening Engine orchestrator
│   ├── scorer.py              # 4-layer scoring system
│   ├── binance_api.py         # Binance REST connector
│   ├── indicators.py          # RSI, MACD, EMA, ADX, ATR, BB, VWAP
│   ├── regime.py              # BULL/BEAR/SIDEWAYS/HIGH_VOL detection
│   ├── patterns.py            # Pattern recognition
│   ├── alpha.py               # 30 alpha factors
│   ├── signals.py             # Signal generation dengan SL/TP
│   ├── database.py            # SQLite untuk signal tracking
│   ├── liquidation.py         # Liquidation heatmap
│   ├── alerter.py             # Alert system
│   └── utils.py               # Helper functions
└── data/
    ├── api.log                # Server logs
    ├── screener.db            # SQLite database (5,685+ signals)
    ├── adaptive_weights.json  # 🆕 RL adaptive weights (auto-generated)
    └── last_scan.json         # Cached scan result
```

---

## ⚙️ Configuration

Edit `config.yaml` untuk kustomisasi:

```yaml
# symbols — Daftar coin yang di-screen (30 default)
symbols:
  - BTCUSDT
  - ETHUSDT
  - SOLUSDT
  - ...

# scan — Interval dan setting
scan:
  interval_minutes: 15        # Auto-scan interval
  kline_limit: 200            # Candles per timeframe

# signal — Threshold untuk sinyal
signal:
  long_min_score: 55          # Threshold untuk LONG
  short_min_score: 55         # Threshold untuk SHORT
  high_confidence: 70         # High confidence threshold

# risk — SL/TP calculation
risk:
  sl_atr_multiplier: 1.5      # SL = entry - (ATR * 1.5)
  tp_atr_multiplier: 3.0      # TP = entry + (ATR * 3.0)
  max_risk_pct: 0.02          # Max 2% risk per trade

# timeframe weights
alpha_weights:
  mean_reversion: 0.25
  momentum: 0.30
  volume: 0.20
  volatility: 0.15
  pattern: 0.10
```

---

## 🔬 Reinforcement Learning System

### Cara Kerja
```python
Every 15 Minutes:
1. Scan coins → Generate signals
2. Check SL/TP outcomes dari scan sebelumnya
3. Analyze: Win Rate, Expectancy, Kelly per regime
4. Adjust: Factor weights yang underperforming
5. Save: Update adaptive_weights.json
6. Apply: Weights baru untuk scan berikutnya
```

### Performance Real Data (3 Days)
| Regime | Trades | Win Rate | Expectancy | Kelly Position |
|--------|--------|----------|------------|----------------|
| **SIDEWAYS** | 2,912 | **61.95%** 🟢 | 0.905 | **20.71%** |
| **HIGH_VOL** | 44 | 54.55% | **2.218** ⭐ | 11.58% |
| BULL | 364 | 50.00% | 0.194 | 4.75% |
| BEAR | 493 | 47.06% | 0.862 | 12.22% |

**Recommendation:** Focus SHORT di SIDEWAYS (63.92% WR), hindari LONG di BULL murni (11% WR)

---

## 📊 Dashboard Views

### 1. Dashboard (Main)
- Scanner table dengan 30 coins
- Signal badges (LONG/SHORT/WAIT)
- Confidence bars
- Liquidation heatmap

### 2. History (NEW!)
- **6 Stats Cards**: Wins/Losses/Open/WR/Best/Worst day
- **🤖 RL Panel**: Adaptive learning insights & factor weights
- **📈 Charts**: Cumulative PNL & Win Rate trend (Chart.js)
- **📅 Calendar**: Interactive dengan daily performance
- **📋 Signal Table**: Filterable (All/TP/SL/Open) + date filter

### 3. Alerts
- Recent signals (20 latest)
- High confidence signals (70%+)
- Near entry signals (within 2%)

---

## 🛠️ Management Commands

```bash
# Using run.sh
./run.sh start       # Start API server
./run.sh stop        # Stop server
./run.sh status      # Check status + health
./run.sh logs        # Follow logs
./run.sh restart     # Restart server
./run.sh once        # Single CLI scan

# Using systemctl (production VPS)
sudo systemctl start coin-screener
sudo systemctl stop coin-screener
sudo systemctl status coin-screener
sudo journalctl -u coin-screener -f
```

---

## 🐛 Troubleshooting

### Port 8000 Already in Use
```bash
# Mac/Linux
lsof -ti:8000 | xargs kill -9
```

### Database Locked
```bash
rm -f data/screener.db-shm data/screener.db-wal
```

### Module Not Found
```bash
pip install -r requirements.txt --force-reinstall
```

### Check Logs
```bash
tail -f data/api.log
grep "ERROR" data/api.log
grep "\[RL\]" data/api.log  # RL updates
```

---

## 📝 Requirements

- **Python**: 3.9+
- **RAM**: 1GB minimum, 2GB recommended
- **Storage**: 100MB (database growth ~10MB/week)
- **Network**: Stable internet (Binance API calls)
- **OS**: Linux/Mac/Windows (WSL)

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

## 🌟 Advanced Features

### Kelly Criterion Position Sizing
Formula Nobel Prize-winning untuk optimal position sizing:
```
f* = (p × b - q) / b

Where:
- p = win probability
- q = loss probability (1-p)  
- b = avg win / avg loss (reward/risk ratio)

Example: SIDEWAYS regime
- Win Rate: 62%
- Avg Win: 2.22%
- Avg Loss: 1.18%
- Kelly: 20.71% position size
```

### Expectancy Calculation
```
Expectancy = (Win% × Avg Win) - (Loss% × Avg Loss)

Positive expectancy = Profitable system
HIGH_VOL expectancy: 2.218 (very profitable!)
```

---

## 🤝 Contributing

1. Fork repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

---

## 📄 License

MIT License — bebas pakai untuk personal atau commercial.

---

## 🙏 Acknowledgments

- Binance API untuk market data
- FastAPI untuk web framework
- Pandas/NumPy untuk data analysis

---

## 📞 Support

Jika ada issues:
1. Check logs: `tail -f data/api.log`
2. Health check: `curl http://localhost:8000/health`
3. RL report: `curl http://localhost:8000/api/rl/report`
4. Buka issue di GitHub

---

**🚀 Ready to trade smarter? Start server dan buka http://localhost:8000!**
