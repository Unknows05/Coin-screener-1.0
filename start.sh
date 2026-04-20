#!/bin/bash
# Coin Screener - Mac & Linux Quick Start Script
# Usage: ./start.sh [start|stop|status|logs]

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="${SCRIPT_DIR}/data/api.pid"
LOGFILE="${SCRIPT_DIR}/data/api.log"
VENV_DIR="${SCRIPT_DIR}/venv"
PORT=8000

# Helper functions
print_status() {
    echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} $1"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

# Check OS
check_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        OS="mac"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        OS="linux"
    else
        OS="unknown"
    fi
}

# Setup virtual environment
setup_venv() {
    if [ ! -d "$VENV_DIR" ]; then
        print_status "Creating virtual environment..."
        python3 -m venv "$VENV_DIR"
        print_success "Virtual environment created"
    fi
    
    # Activate
    source "$VENV_DIR/bin/activate"
    
    # Install/update requirements
    if [ ! -f "$VENV_DIR/.requirements_installed" ] || [ requirements.txt -nt "$VENV_DIR/.requirements_installed" ]; then
        print_status "Installing dependencies..."
        pip install --upgrade pip -q
        pip install -r requirements.txt -q
        touch "$VENV_DIR/.requirements_installed"
        print_success "Dependencies installed"
    fi
}

# Check if port is in use
check_port() {
    if lsof -ti:$PORT > /dev/null 2>&1; then
        return 0  # Port in use
    else
        return 1  # Port free
    fi
}

# Kill process on port
kill_port() {
    print_warning "Port $PORT is in use, killing process..."
    lsof -ti:$PORT | xargs kill -9 2>/dev/null || true
    sleep 2
}

# Start server
start_server() {
    print_status "Starting Coin Screener Pro..."
    
    # Check if already running
    if [ -f "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE")
        if kill -0 "$PID" 2>/dev/null; then
            print_warning "Server already running (PID: $PID)"
            print_status "Access: http://localhost:$PORT"
            return
        else
            rm -f "$PIDFILE"
        fi
    fi
    
    # Check/kill port if in use
    if check_port; then
        kill_port
    fi
    
    # Setup environment
    cd "$SCRIPT_DIR"
    mkdir -p data
    
    # Setup virtual environment
    setup_venv
    
    # Mac-specific: Fix SSL certificates if needed
    if [ "$OS" == "mac" ]; then
        export SSL_CERT_FILE=$(python3 -c "import certifi; print(certifi.where())" 2>/dev/null || echo "")
    fi
    
    # Start server
    print_status "Launching FastAPI server on port $PORT..."
    nohup python api.py > "$LOGFILE" 2>&1 &
    PID=$!
    echo "$PID" > "$PIDFILE"
    
    # Wait for server to start
    print_status "Waiting for server to start..."
    sleep 3
    
    # Health check
    for i in {1..10}; do
        if curl -s http://localhost:$PORT/health > /dev/null 2>&1; then
            print_success "Server started successfully!"
            print_status "Dashboard: http://localhost:$PORT"
            print_status "API Docs:  http://localhost:$PORT/docs"
            print_status "PID: $PID"
            print_status "Logs: tail -f $LOGFILE"
            
            # Mac: Open browser automatically
            if [ "$OS" == "mac" ]; then
                sleep 1
                open "http://localhost:$PORT" 2>/dev/null || true
            fi
            
            return
        fi
        sleep 1
    done
    
    print_error "Server failed to start within 10 seconds"
    print_status "Check logs: tail -f $LOGFILE"
    exit 1
}

# Stop server
stop_server() {
    print_status "Stopping server..."
    
    if [ -f "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null || true
            sleep 2
            # Force kill if still running
            if kill -0 "$PID" 2>/dev/null; then
                kill -9 "$PID" 2>/dev/null || true
            fi
            print_success "Server stopped (PID: $PID)"
        else
            print_warning "Process $PID not found"
        fi
        rm -f "$PIDFILE"
    else
        print_warning "No PID file found"
        # Try to kill any process on port
        if check_port; then
            kill_port
            print_success "Killed process on port $PORT"
        fi
    fi
}

# Check status
check_status() {
    if [ -f "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE")
        if kill -0 "$PID" 2>/dev/null; then
            print_success "Server is running (PID: $PID)"
            
            # Get uptime
            if [ "$OS" == "mac" ]; then
                UPTIME=$(ps -o etime= -p "$PID" 2>/dev/null | xargs)
            else
                UPTIME=$(ps -o etime= -p "$PID" 2>/dev/null | xargs)
            fi
            
            if [ -n "$UPTIME" ]; then
                print_status "Uptime: $UPTIME"
            fi
            
            # Health check
            HEALTH=$(curl -s http://localhost:$PORT/health 2>/dev/null)
            if [ -n "$HEALTH" ]; then
                print_status "Health: $HEALTH"
            fi
            
            print_status "URL: http://localhost:$PORT"
        else
            print_error "Server not running (stale PID: $PID)"
            rm -f "$PIDFILE"
        fi
    else
        if check_port; then
            print_warning "Server may be running (port $PORT in use) but no PID file"
            print_status "URL: http://localhost:$PORT"
        else
            print_error "Server is not running"
        fi
    fi
}

# View logs
view_logs() {
    if [ -f "$LOGFILE" ]; then
        print_status "Showing logs (Ctrl+C to exit)..."
        tail -f "$LOGFILE"
    else
        print_error "No log file found"
    fi
}

# Quick RL report
rl_report() {
    print_status "Fetching RL Performance Report..."
    curl -s http://localhost:$PORT/api/rl/report 2>/dev/null | head -50 || print_error "Server not running"
}

# Single scan
single_scan() {
    print_status "Running single scan..."
    cd "$SCRIPT_DIR"
    setup_venv
    python screen_once.py
}

# Main menu
show_help() {
    echo ""
    echo "Coin Screener Pro - Mac & Linux Manager"
    echo "======================================"
    echo ""
    echo "Usage: ./start.sh [command]"
    echo ""
    echo "Commands:"
    echo "  start       Start the API server (default)"
    echo "  stop        Stop the API server"
    echo "  restart     Restart the server"
    echo "  status      Check server status and health"
    echo "  logs        View server logs (follow mode)"
    echo "  scan        Run single screening (CLI)"
    echo "  rl          Show RL performance report"
    echo "  setup       Setup virtual environment only"
    echo "  help        Show this help message"
    echo ""
    echo "Examples:"
    echo "  ./start.sh           # Start server"
    echo "  ./start.sh status    # Check status"
    echo "  ./start.sh rl        # View RL report"
    echo ""
}

# Main
main() {
    check_os
    
    case "${1:-start}" in
        start)
            start_server
            ;;
        stop)
            stop_server
            ;;
        restart)
            stop_server
            sleep 2
            start_server
            ;;
        status)
            check_status
            ;;
        logs)
            view_logs
            ;;
        scan)
            single_scan
            ;;
        rl)
            rl_report
            ;;
        setup)
            setup_venv
            print_success "Setup complete!"
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            print_error "Unknown command: $1"
            show_help
            exit 1
            ;;
    esac
}

# Run main
main "$@"
