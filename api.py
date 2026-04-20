"""
Coin Screener API Server — FastAPI + Background Scheduler
Exposes screening engine as REST API on localhost:8000.
Includes Error Hardening and Single Scan Endpoint.
"""
import sys
import yaml
import logging
import asyncio
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse, FileResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.engine import ScreeningEngine
from src.liquidation import liquidation_heatmap, update_liquidation_data

# ---- Globals ----
engine = None
scheduler = None
config = {}
_background_tasks = set()  # Track fire-and-forget tasks

# ---- Logging ----
Path("data").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("data/api.log"),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

# ---- Lifecycle ----

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    global engine, scheduler, config

    # Load config
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    # Initialize engine
    engine = ScreeningEngine(config, cache_dir="data")
    logger.info("[API] ScreeningEngine initialized")

    # Start scheduler (runs in background)
    scheduler = AsyncIOScheduler()
    interval_minutes = config.get("scan", {}).get("interval_minutes", 15)

    scheduler.add_job(
        auto_scan,
        "interval",
        minutes=interval_minutes,
        id="auto_scan",
        name=f"Auto-screen every {interval_minutes} minutes",
        replace_existing=True,
    )
    scheduler.start()

    # Update engine with next scan time
    next_run = datetime.now() + timedelta(minutes=interval_minutes)
    engine.set_next_scan(next_run.isoformat())

    logger.info(
        f"[API] Scheduler started — scanning every {interval_minutes} minutes"
    )
    logger.info(f"[API] Next scan at: {next_run.strftime('%H:%M:%S')}")

    yield  # Server is running

    # Shutdown
    logger.info("[API] Shutting down...")
    if scheduler:
        scheduler.shutdown(wait=False)
    if engine:
        engine.close()
    logger.info("[API] Shutdown complete")


# ---- Background Task ----

async def auto_scan():
    """Background auto-scan job."""
    try:
        logger.info("[Scheduler] Triggering auto-scan...")
        result = engine.scan()
        if result.get("ok"):
            # Update next scan time
            interval = config.get("scan", {}).get("interval_minutes", 15)
            next_run = datetime.now() + timedelta(minutes=interval)
            engine.set_next_scan(next_run.isoformat())
            logger.info(
                f"[Scheduler] Auto-scan complete: {result['summary']} "
                f"(next: {next_run.strftime('%H:%M:%S')})"
            )
        else:
            logger.error(f"[Scheduler] Auto-scan failed: {result.get('error')}")
    except Exception as e:
        logger.error(f"[Scheduler] Auto-scan error: {e}")


# ---- FastAPI App ----

app = FastAPI(
    title="Coin Screener API",
    description="Deep screening system for Binance USDT-M Futures.",
    version="2.0.0",
    lifespan=lifespan,
)


# ---- API Endpoints ----

@app.get("/health")
async def health():
    return {"status": "ok", "service": "coin-screener", "version": "2.0.0"}


@app.post("/api/scan")
async def trigger_scan():
    """Trigger an immediate screening scan."""
    if engine.is_scanning():
        return {"ok": False, "error": "Scan already in progress"}
    # Run scan in background with task tracking
    task = asyncio.create_task(engine.scan())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"ok": True, "message": "Scan triggered"}


@app.get("/api/scan/latest")
async def get_latest_scan():
    """Get the most recent scan result."""
    try:
        result = engine.get_latest_scan()
        if result is None:
            return JSONResponse(status_code=404, content={"ok": False, "error": "No scan data available."})
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/signals")
async def get_signals():
    """Get only coins with active LONG or SHORT signals."""
    return engine.get_signals()


@app.get("/api/status")
async def get_status():
    """Get system status."""
    return engine.get_status()


@app.get("/api/alerts")
async def get_alerts(limit: int = 50):
    """
    Get Active Signals (Synced with Dashboard).
    Returns signals that are currently OPEN (not WAIT).
    """
    try:
        # Get latest scan result
        scan_result = engine.get_latest_scan()
        if not scan_result or not scan_result.get("ok"):
            return {"ok": True, "data": []}
            
        # Filter only active signals (LONG or SHORT)
        active_signals = [s for s in scan_result.get("data", []) if s.get("signal") in ("LONG", "SHORT")]
        
        # Sort by confidence descending
        active_signals.sort(key=lambda x: x.get("confidence", 0), reverse=True)
        
        return {"ok": True, "data": active_signals[:limit]}
    except Exception as e:
        logger.error(f"Alert API Error: {e}")
        return {"ok": True, "data": []}


@app.get("/api/db/stats")
async def get_db_stats():
    """Get overall statistics."""
    try:
        return {"ok": True, "stats": engine.get_db_stats()}
    except Exception:
        return {"ok": True, "stats": {"wins": 0, "losses": 0, "win_rate": 0}}


@app.get("/api/rl/performance")
async def get_rl_performance(days: int = 7):
    """
    Get RL adaptive performance analysis.
    Returns performance metrics per regime with Kelly Criterion and Expectancy.
    """
    try:
        # Use engine's RL optimizer directly (avoid duplicate DB connections)
        optimizer = engine.rl_optimizer
        performance = optimizer.analyze_recent_performance(days=days)
        recommendations = {}
        
        # Generate recommendations per regime
        for regime, data in performance.items():
            if "error" not in data:
                recommendations[regime] = optimizer.get_recommended_params(regime)
        
        return {
            "ok": True,
            "days_analyzed": days,
            "performance": performance,
            "recommendations": recommendations,
            "current_weights": optimizer.current_weights,
        }
    except Exception as e:
        logger.error(f"RL Performance API Error: {e}")
        return {"ok": False, "error": str(e)}


@app.get("/api/rl/report")
async def get_rl_report():
    """Get human-readable RL analysis report."""
    try:
        optimizer = engine.rl_optimizer
        report = optimizer.generate_report()
        return {"ok": True, "report": report}
    except Exception as e:
        logger.error(f"RL Report API Error: {e}")
        return {"ok": False, "error": str(e)}


@app.get("/api/risk/status")
async def get_risk_status():
    """
    Get comprehensive risk management status.
    Returns circuit breaker status, overfitting checks, and risk metrics.
    """
    try:
        from src.risk_manager import get_risk_manager
        rm = get_risk_manager()
        report = rm.get_status_report()
        return {"ok": True, "risk_status": report}
    except Exception as e:
        logger.error(f"Risk Status API Error: {e}")
        return {"ok": False, "error": str(e)}


@app.post("/api/risk/reset")
async def reset_circuit_breaker(manual: bool = True):
    """
    Reset circuit breaker (requires authentication in production).
    """
    try:
        from src.risk_manager import get_risk_manager
        rm = get_risk_manager()
        success = rm.black_swan_protector.reset_circuit_breaker(manual=manual)
        if success:
            return {"ok": True, "message": "Circuit breaker reset successfully"}
        else:
            return {"ok": False, "error": "Could not reset circuit breaker - cooldown period active"}
    except Exception as e:
        logger.error(f"Risk Reset API Error: {e}")
        return {"ok": False, "error": str(e)}


@app.get("/api/signals/history")
async def get_signals_history(limit: int = 100):
    """
    Get signal history from database with SL/TP outcomes.
    Returns: list of signals with result (WIN/LOSS/OPEN), exit_price, exit_reason
    """
    try:
        signals = engine.get_signals_history(limit)
        return {"ok": True, "data": signals, "count": len(signals)}
    except Exception as e:
        logger.error(f"Signals History API Error: {e}")
        return {"ok": False, "error": str(e), "data": []}


@app.get("/api/calendar/{year}/{month}")
async def get_calendar(year: int, month: int):
    """
    Get calendar view for a month.
    Returns data formatted for Chart.js and Calendar UI.
    """
    try:
        calendar_data = engine.get_calendar(year, month)
        
        # Prepare data for Chart.js (Cumulative PNL & Win Rate)
        chart_data = {
            "labels": [],
            "pnl": [],      # Cumulative Performance Score
            "win_rate": []  # Daily Win Rate %
        }
        
        if calendar_data:
            # Sort by date
            sorted_data = sorted(calendar_data, key=lambda x: x.get("scan_date", ""))
            
            cumulative_score = 0
            for day in sorted_data:
                date_str = day.get("scan_date", "")[5:] # Format: MM-DD
                wr = day.get("win_rate", 0) or 0
                wins = day.get("wins", 0) or 0
                losses = day.get("losses", 0) or 0
                
                # Calculate daily score impact (Wins add, Losses subtract)
                daily_score = wins - losses
                cumulative_score += daily_score
                
                chart_data["labels"].append(date_str)
                chart_data["pnl"].append(cumulative_score)
                chart_data["win_rate"].append(wr)
        
        return {"ok": True, "calendar": calendar_data, "chart_data": chart_data}
    except Exception as e:
        logger.error(f"Calendar API Error: {e}")
        return {"ok": True, "calendar": [], "chart_data": {"labels": [], "pnl": [], "win_rate": []}}


@app.get("/api/refresh")
async def refresh_data():
    """
    Soft Reset: Clears cache and triggers a fresh scan.
    This is the "Force Refresh" button action.
    """
    try:
        if engine.is_scanning():
            return {"ok": False, "error": "Scan already in progress"}
        engine.clear_cache()
        # Trigger scan in background with task tracking
        task = asyncio.create_task(engine.scan())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        return {"ok": True, "message": "Cache cleared and new scan started"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/scan/single/{symbol}")
async def scan_single_coin(symbol: str):
    """
    Deep Scan for a single coin (Used for Volatile Interaction).
    Performs a full scan (indicators, patterns) for just one coin.
    """
    try:
        symbol = symbol.upper()
        if not symbol.endswith("USDT"):
            symbol += "USDT"

        result = engine.scan_single_symbol(symbol)
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _is_leveraged_token(symbol: str) -> bool:
    """Check if symbol is a leveraged token (UP/DOWN/BULL/BEAR)."""
    return any(s in symbol for s in ("UP", "DOWN", "BULL", "BEAR"))


@app.get("/api/volatile")
async def get_volatile_coins():
    """
    Detects 'Whale Accumulation' using Volume/Price Divergence.
    Uses async HTTP client for non-blocking I/O.
    """
    url = "https://fapi.binance.com/fapi/v1/ticker/24hr"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
            response.raise_for_status()
            tickers = response.json()

        anomalies = []
        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            if _is_leveraged_token(sym):
                continue

            quote_vol = float(t.get("quoteVolume", 0))
            price_change = float(t.get("priceChangePercent", 0))

            if quote_vol < 5_000_000:
                continue

            # Whale Anomaly Logic
            anomaly_score = quote_vol / (abs(price_change) + 1)
            if anomaly_score > 1_000_000:
                anomalies.append({
                    "symbol": sym,
                    "price": float(t["lastPrice"]),
                    "volume_24h": quote_vol,
                    "change_24h": price_change,
                    "score": anomaly_score
                })

        anomalies.sort(key=lambda x: x["score"], reverse=True)
        return {"ok": True, "data": anomalies[:15]}
    except Exception as e:
        logger.error(f"Volatile API Error: {e}")
        return {"ok": False, "data": []}


# ---- Liquidation Heatmap Endpoints ----

@app.get("/api/liquidations")
async def get_liquidations():
    """
    Get liquidation heatmap data from Binance.
    Returns top liquidated symbols with long/short breakdown.
    """
    try:
        # Fetch fresh data if needed
        if not liquidation_heatmap.heatmap_data:
            await update_liquidation_data()
        
        heatmap = liquidation_heatmap.calculate_heatmap()
        summary = liquidation_heatmap.get_summary()
        
        return {
            "ok": True,
            "summary": summary,
            "heatmap": heatmap.get("heatmap", []),
            "total_value": heatmap.get("total_value", 0),
            "updated_at": heatmap.get("updated_at")
        }
    except Exception as e:
        logger.error(f"Liquidations API Error: {e}")
        return {"ok": False, "error": str(e), "heatmap": []}


@app.get("/api/liquidations/refresh")
async def refresh_liquidations():
    """Force refresh liquidation data."""
    try:
        await update_liquidation_data()
        heatmap = liquidation_heatmap.calculate_heatmap()
        return {
            "ok": True,
            "message": "Liquidation data refreshed",
            "total_value": heatmap.get("total_value", 0)
        }
    except Exception as e:
        logger.error(f"Refresh Liquidations Error: {e}")
        return {"ok": False, "error": str(e)}


@app.get("/api/liquidations/symbol/{symbol}")
async def get_symbol_liquidations(symbol: str):
    """Get liquidation levels for a specific symbol."""
    try:
        symbol = symbol.upper().replace("USDT", "")
        levels = await liquidation_heatmap.get_liquidation_levels(symbol)
        return {"ok": True, "data": levels}
    except Exception as e:
        logger.error(f"Symbol Liquidations Error: {e}")
        return {"ok": False, "error": str(e)}


# ---- Dashboard Route (FIXED) ----

@app.get("/", response_class=FileResponse)
async def read_root():
    """Serves the main dashboard HTML."""
    return "static/dashboard.html"


# ---- Run ----

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api:app",
        host="127.0.0.1",  # Localhost only for security
        port=8000,
        reload=False,
        log_level="info"
    )
