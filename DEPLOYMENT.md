# 🚀 Deployment Guide

Panduan lengkap deploy Coin Screener Pro ke berbagai platform.

## 📑 Table of Contents
- [Local Development (Mac/Linux)](#local-development-maclinux)
- [VPS/Cloud Deployment](#vpscloud-deployment)
- [Docker Deployment](#docker-deployment)
- [Environment Variables](#environment-variables)
- [Troubleshooting](#troubleshooting)

---

## 💻 Local Development (Mac/Linux)

### Prerequisites
- Python 3.9+
- pip atau conda
- Git

### 1. Clone Repository
```bash
git clone https://github.com/Unknows05/Coin-screener-1.0.git
cd Coin-screener-1.0
```

### 2. Setup Virtual Environment (Recommended)
```bash
# Mac/Linux
python3 -m venv venv
source venv/bin/activate

# atau dengan conda
conda create -n screener python=3.11
conda activate screener
```

### 3. Install Dependencies
```bash
# Standard install
pip install -r requirements.txt

# Mac dengan Homebrew Python (jika ada permission issue)
pip install --user -r requirements.txt

# Linux dengan system packages (tidak direkomendasikan untuk production)
pip install --break-system-packages -r requirements.txt
```

### 4. Run Server
```bash
# Menggunakan run.sh (Recommended)
chmod +x run.sh
./run.sh start

# atau manual
python3 api.py

# atau dengan uvicorn (for development)
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

### 5. Access Dashboard
```
http://localhost:8000
```

### Mac-Specific Notes
```bash
# Jika ada SSL certificate issues di Mac
export SSL_CERT_FILE=$(python3 -c "import certifi; print(certifi.where())")

# Jika port 8000 sudah dipakai
lsof -ti:8000 | xargs kill -9
```

---

## ☁️ VPS/Cloud Deployment

### Option 1: VPS (DigitalOcean, AWS EC2, Linode, Vultr)

#### Step 1: Provision VPS
- **OS**: Ubuntu 22.04 LTS (Recommended)
- **Size**: 1GB RAM minimum, 2GB recommended
- **Ports**: 22 (SSH), 8000 (App)

#### Step 2: SSH ke VPS
```bash
ssh root@YOUR_VPS_IP
```

#### Step 3: Install Dependencies
```bash
# Update system
apt update && apt upgrade -y

# Install Python & dependencies
apt install -y python3 python3-pip python3-venv git curl

# Install additional tools
apt install -y sqlite3 htop
```

#### Step 4: Setup Application
```bash
# Create user (security best practice)
useradd -m -s /bin/bash screener
usermod -aG sudo screener
su - screener

# Clone repo
cd ~
git clone https://github.com/Unknows05/Coin-screener-1.0.git
cd Coin-screener-1.0

# Setup virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create data directory
mkdir -p data
```

#### Step 5: Systemd Service (Production)
```bash
# Create systemd service file
sudo tee /etc/systemd/system/coin-screener.service > /dev/null << 'EOF'
[Unit]
Description=Coin Screener Pro API
After=network.target

[Service]
Type=simple
User=screener
WorkingDirectory=/home/screener/Coin-screener-1.0
Environment="PATH=/home/screener/Coin-screener-1.0/venv/bin"
ExecStart=/home/screener/Coin-screener-1.0/venv/bin/python -u api.py
Restart=always
RestartSec=10
StandardOutput=append:/home/screener/Coin-screener-1.0/data/api.log
StandardError=append:/home/screener/Coin-screener-1.0/data/api.log

[Install]
WantedBy=multi-user.target
EOF

# Enable dan start service
sudo systemctl daemon-reload
sudo systemctl enable coin-screener
sudo systemctl start coin-screener

# Check status
sudo systemctl status coin-screener
```

#### Step 6: Setup Firewall
```bash
# Allow SSH dan HTTP
ufw allow 22/tcp
ufw allow 8000/tcp
ufw enable
```

#### Step 7: Reverse Proxy dengan Nginx (Optional untuk domain)
```bash
# Install nginx
sudo apt install nginx

# Setup reverse proxy
sudo tee /etc/nginx/sites-available/coin-screener > /dev/null << 'EOF'
server {
    listen 80;
    server_name your-domain.com;  # atau VPS_IP

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
    }
}
EOF

# Enable site
sudo ln -s /etc/nginx/sites-available/coin-screener /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
```

---

### Option 2: Cloud Run / Serverless (Advanced)

Untuk platform cloud-native, gunakan Docker:

#### Dockerfile
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create data directory
RUN mkdir -p data

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run application
CMD ["python", "api.py"]
```

#### Build & Push
```bash
# Build image
docker build -t coin-screener:latest .

# Tag untuk registry
docker tag coin-screener:latest your-registry/coin-screener:latest

# Push
docker push your-registry/coin-screener:latest
```

---

## 🐳 Docker Deployment

### Quick Start dengan Docker
```bash
# Build image
docker build -t coin-screener .

# Run container
docker run -d \
  --name coin-screener \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  coin-screener

# View logs
docker logs -f coin-screener
```

### Docker Compose (Recommended)
```yaml
# docker-compose.yml
version: '3.8'

services:
  screener:
    build: .
    container_name: coin-screener
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
      - ./config.yaml:/app/config.yaml
    environment:
      - PYTHONUNBUFFERED=1
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

Run dengan:
```bash
docker-compose up -d
```

---

## 🔧 Environment Variables

Buat file `.env` di root directory:

```bash
# .env file
# Server Configuration
PORT=8000
HOST=0.0.0.0

# Logging
LOG_LEVEL=INFO

# Database
DB_PATH=data/screener.db

# Scanning
SCAN_INTERVAL_MINUTES=15

# RL Configuration
RL_LEARNING_RATE=0.05
RL_MIN_SAMPLES=20
```

---

## 🐛 Troubleshooting

### Port Already in Use
```bash
# Find process using port 8000
sudo lsof -i :8000

# Kill process
kill -9 <PID>
```

### Permission Denied (Linux/Mac)
```bash
# Fix permissions
chmod +x run.sh
chmod 755 data/

# Run dengan user yang benar
sudo chown -R $USER:$USER data/
```

### Database Locked (SQLite)
```bash
# Fix WAL mode
sqlite3 data/screener.db "PRAGMA wal_checkpoint;"
rm -f data/screener.db-shm data/screener.db-wal
```

### Module Not Found
```bash
# Reinstall dependencies
pip install -r requirements.txt --force-reinstall
```

### Rate Limiting Issues
```bash
# Check if IP blocked
curl -I https://fapi.binance.com/fapi/v1/ping

# Use proxy (if needed)
export HTTP_PROXY=http://proxy:port
export HTTPS_PROXY=http://proxy:port
```

### Memory Issues (VPS dengan RAM kecil)
```bash
# Create swap file
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Make permanent
sudo echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

---

## 📊 Monitoring

### Check Server Status
```bash
# Health check
curl http://localhost:8000/health

# System status
curl http://localhost:8000/api/status

# Database stats
curl http://localhost:8000/api/db/stats

# RL Performance
curl http://localhost:8000/api/rl/performance?days=7
```

### Log Monitoring
```bash
# Real-time logs
tail -f data/api.log

# Filter errors
grep "ERROR" data/api.log

# Filter RL updates
grep "\[RL\]" data/api.log
```

---

## 🔒 Security Best Practices

### 1. Firewall Setup
```bash
# Only allow necessary ports
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp    # SSH
ufw allow 8000/tcp  # App
ufw enable
```

### 2. Fail2Ban (Brute force protection)
```bash
sudo apt install fail2ban
sudo systemctl enable fail2ban
```

### 3. Regular Updates
```bash
# Setup automatic security updates
sudo apt install unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

---

## 📚 Next Steps

Setelah deploy berhasil:
1. Buka dashboard di browser: `http://YOUR_IP:8000`
2. Check tab **History** untuk melihat RL Panel
3. Monitor signal performance di `/api/rl/performance`
4. Setup cron job untuk backup database (opsional)

---

## 🆘 Support

Jika ada issues:
1. Check logs: `tail -f data/api.log`
2. Health check: `curl http://localhost:8000/health`
3. Restart service: `sudo systemctl restart coin-screener`
