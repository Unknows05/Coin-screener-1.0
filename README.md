# 📊 Coin Screener API

Deep screening system for Binance USDT-M Futures. **No API key needed.**

## Fitur

- 🔍 **Deep Screening** — 30 alpha factors, regime detection, pattern recognition
- 🧠 **Multi-Timeframe** — 15m + 1h + 4h weighted scoring
- 📐 **Pattern Recognition** — Triangle, Flag, Double Top/Bottom, Breakout
- 🎯 **Signal Generation** — LONG/SHORT/WAIT + confidence, entry, SL, TP
- 🔄 **Auto-Scan** — Every 15 minutes (configurable)
- 🌐 **REST API** — FastAPI with Swagger docs
- 💾 **Result Cache** — In-memory + file persistence

## Quick Start

```bash
cd ~/Desktop/coin-screener

# Install
pip install --break-system-packages -r requirements.txt

# Start API server
./run.sh start

# Check status
./run.sh status

# View logs
./run.sh logs
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/docs` | Swagger UI (auto-generated docs) |
| POST | `/api/scan` | Trigger screening now (non-blocking) |
| GET | `/api/scan/latest` | Get latest scan result |
| GET | `/api/signals` | Only LONG/SHORT signals |
| GET | `/api/coin/{SYMBOL}` | Detail for specific coin |
| GET | `/api/status` | System status & metadata |

## Examples

```bash
# Health check
curl http://localhost:8000/health

# Get all signals
curl http://localhost:8000/api/signals

# Get latest full scan
curl http://localhost:8000/api/scan/latest

# Get BTC detail
curl http://localhost:8000/api/coin/BTCUSDT

# Trigger manual scan
curl -X POST http://localhost:8000/api/scan

# System status
curl http://localhost:8000/api/status
```

## Response Format

```json
// GET /api/signals
{
  "ok": true,
  "timestamp": "2026-04-13T01:19:24",
  "elapsed_seconds": 16.6,
  "data": [
    {
      "symbol": "BTCUSDT",
      "price": 71073.1,
      "signal": "LONG",
      "confidence": 78,
      "entry": 71073.1,
      "sl": 69400,
      "tp": 73600,
      "regime": "BULL",
      "score": 78,
      "reasons": ["Strong composite score", "Bullish regime"]
    }
  ],
  "summary": {
    "total": 30,
    "long": 12,
    "short": 5,
    "wait": 13,
    "active_signals": 17
  }
}
```

## Architecture

```
┌──────────────────────────────────────────────┐
│  FastAPI Server (localhost:8000)             │
│                                              │
│  Background:                                 │
│  ├─ APScheduler → scan every 15 min          │
│  ├─ Result cached in memory + file           │
│  └─ Auto-restart via systemd (optional)      │
│                                              │
│  API → reads from cache (non-blocking)       │
│  POST /api/scan → triggers background scan   │
└──────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────┐
│  Screening Engine (src/engine.py)            │
│  ├─ BinanceFuturesAPI (public endpoints)     │
│  ├─ Scorer (alpha + regime + pattern)        │
│  └─ Signal Generator (LONG/SHORT/WAIT)       │
└──────────────────────────────────────────────┘
```

## Structure

```
coin-screener/
├── api.py                   # FastAPI server + scheduler
├── src/
│   ├── engine.py            # ScreeningEngine orchestrator
│   ├── binance_api.py       # Binance REST connector
│   ├── indicators.py        # RSI, MACD, EMA, ADX, ATR, BB, VWAP, OBV
│   ├── regime.py            # BULL/BEAR/SIDEWAYS/HIGH_VOL
│   ├── patterns.py          # Triangle, Flag, Double Top/Bottom
│   ├── alpha.py             # 30 alpha factors
│   ├── scorer.py            # Multi-timeframe scoring
│   ├── signals.py           # Signal generation
│   └── display.py           # Console output (rich)
├── config.yaml              # Configuration
├── requirements.txt
├── run.sh                   # start/stop/status/logs/once
├── screen_once.py           # CLI single scan
└── data/
    ├── api.log              # Server log
    └── last_scan.json       # Cached scan result
```

## Config

Edit `config.yaml` untuk ubah:
- **symbols** — Coin list
- **scan.interval_minutes** — Auto-scan interval
- **signal.long_min_score** — Threshold untuk LONG
- **risk.sl_atr_multiplier** — SL distance
- **risk.tp_atr_multiplier** — TP distance

## systemd (Auto-start on boot)

```bash
# Create service
sudo tee /etc/systemd/system/coin-screener.service > /dev/null << 'EOF'
[Unit]
Description=Coin Screener API
After=network.target

[Service]
Type=simple
User=febrian
WorkingDirectory=/home/febrian/Desktop/coin-screener
ExecStart=/usr/bin/python3 -u api.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable coin-screener
sudo systemctl start coin-screener
sudo systemctl status coin-screener
```
