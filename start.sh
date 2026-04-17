#!/bin/bash
cd /home/febrian/Desktop/coin-screener

# Kill any existing instances
pkill -f "python3.*api.py" 2>/dev/null
sleep 1

# Start server in background with output logging
python3 -u api.py > data/api.log 2>&1 &
SERVER_PID=$!

# Wait for server to be ready
echo "Waiting for server to start..."
for i in {1..30}; do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo "✅ Server is ready! (PID: $SERVER_PID)"
        echo "Dashboard: http://localhost:8000"
        break
    fi
    sleep 1
done

# Keep script alive to prevent background process from being killed
wait $SERVER_PID
