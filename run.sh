#!/bin/bash
# Coin Screener API — Manager Script
# Usage: ./run.sh [start|stop|status|logs|once|restart]

PIDFILE="data/api.pid"
LOGFILE="data/api.log"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$SCRIPT_DIR/data"

start() {
    if [ -f "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "🟢 API server already running (PID: $PID)"
            return
        else
            rm -f "$PIDFILE"
        fi
    fi

    echo "🚀 Starting Coin Screener API on port 8000..."
    cd "$SCRIPT_DIR"
    nohup python3 -u api.py > "$LOGFILE" 2>&1 &
    PID=$!
    echo "$PID" > "$PIDFILE"
    echo "✅ API server started (PID: $PID)"
    echo "📊 Logs: tail -f $LOGFILE"
    echo "🌐 Docs: http://localhost:8000/docs"

    # Wait for server to boot
    sleep 3
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo "✅ Server is healthy"
    else
        echo "⚠️  Server may still be starting up..."
    fi
}

stop() {
    if [ -f "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "⏹️  Stopping API server (PID: $PID)..."
            kill "$PID"
            sleep 2
            # Force kill if still running
            kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null
            rm -f "$PIDFILE"
            echo "✅ API server stopped"
        else
            echo "⚠️  Process $PID not found. Cleaning up..."
            rm -f "$PIDFILE"
        fi
    else
        echo "⚠️  API server not running (no PID file)"
    fi
}

status() {
    if [ -f "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE")
        if kill -0 "$PID" 2>/dev/null; then
            UPTIME=$(ps -o etime= -p "$PID" 2>/dev/null | xargs)
            echo "🟢 API server running (PID: $PID, Uptime: $UPTIME)"
            # Check health
            HEALTH=$(curl -s http://localhost:8000/health 2>/dev/null)
            if [ -n "$HEALTH" ]; then
                echo "   Health: $HEALTH"
            fi
        else
            echo "🔴 API server not running (stale PID: $PID)"
            rm -f "$PIDFILE"
        fi
    else
        echo "🔴 API server not running"
    fi
}

logs() {
    if [ -f "$LOGFILE" ]; then
        tail -f "$LOGFILE"
    else
        echo "⚠️  No log file found at $LOGFILE"
    fi
}

once() {
    echo "🔍 Running single screening..."
    cd "$SCRIPT_DIR"
    python3 screen_once.py
}

case "${1:-start}" in
    start)   start ;;
    stop)    stop ;;
    status)  status ;;
    logs)    logs ;;
    once)    once ;;
    restart) stop; sleep 2; start ;;
    *)
        echo "Usage: $0 {start|stop|status|logs|once|restart}"
        echo ""
        echo "Commands:"
        echo "  start     — Start API server on port 8000"
        echo "  stop      — Stop API server"
        echo "  status    — Check if server is running + health"
        echo "  logs      — Follow log output"
        echo "  once      — Run single screening (CLI output)"
        echo "  restart   — Restart API server"
        echo ""
        echo "API Endpoints (once started):"
        echo "  GET  /health              — Health check"
        echo "  GET  /docs                — Swagger UI (auto-generated)"
        echo "  POST /api/scan            — Trigger screening"
        echo "  GET  /api/scan/latest     — Last scan result"
        echo "  GET  /api/signals         — Only LONG/SHORT signals"
        echo "  GET  /api/coin/{SYMBOL}   — Detail per coin"
        echo "  GET  /api/status          — System status"
        ;;
esac
