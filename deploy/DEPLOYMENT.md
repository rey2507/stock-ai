# Paper Trader — Linux VM + Cloudflare Tunnel Deployment Guide

## Overview

This guide deploys the Paper Trader FastAPI application on a Linux VM with Cloudflare Tunnel for public HTTPS access.

```
Internet → Cloudflare (DDoS, TLS, WAF) → Cloudflare Tunnel → VM:8000 (FastAPI + SQLite)
```

## Prerequisites

- Linux VM (Ubuntu 22.04/24.04 recommended)
- Root/sudo access
- Domain name (for Cloudflare DNS)
- Cloudflare account
- Angel One SmartAPI credentials

## Step 1: Prepare the VM

### 1.1 Update system
```bash
sudo apt-get update && apt-get upgrade -y
```

### 1.2 Install dependencies
```bash
sudo apt-get install -y python3.13 python3.13-venv python3.13-dev git curl
```

### 1.3 Create app user and directories
```bash
sudo useradd -r -s /bin/false -d /opt/paper-trader paper-trader
sudo mkdir -p /opt/paper-trader/data /opt/paper-trader/logs
sudo chown -R paper-trader:paper-trader /opt/paper-trader
```

## Step 2: Deploy Application

### 2.1 Clone repository
```bash
cd /opt/paper-trader
sudo -u paper-trader git clone https://github.com/rey2507/stock-ai.git .
```

### 2.2 Create virtual environment
```bash
sudo -u paper-trader python3.13 -m venv /opt/paper-trader/.venv
```

### 2.3 Install Python dependencies
```bash
sudo -u paper-trader /opt/paper-trader/.venv/bin/pip install --upgrade pip
sudo -u paper-trader /opt/paper-trader/.venv/bin/pip install -r /opt/paper-trader/requirements.txt
```

### 2.4 Configure environment variables
```bash
sudo cp /opt/paper-trader/deploy/paper-trader.env /etc/paper-trader.env
sudo chmod 600 /etc/paper-trader.env
sudo chown paper-trader:paper-trader /etc/paper-trader.env
```

**Edit `/etc/paper-trader.env` with your credentials:**
```bash
sudo nano /etc/paper-trader.env
```

Required:
```env
ANGEL_API_KEY=your_api_key
ANGEL_CLIENT_CODE=your_client_code
ANGEL_PIN=your_pin
ANGEL_TOTP_SECRET=your_totp_secret
```

### 2.5 Initialize database
```bash
sudo -u paper-trader /opt/paper-trader/.venv/bin/python -c "from backend.db import init_db; init_db()"
```

### 2.6 Install systemd service
```bash
sudo cp /opt/paper-trader/deploy/paper-trader.service /etc/systemd/system/paper-trader.service
sudo systemctl daemon-reload
sudo systemctl enable --now paper-trader
```

### 2.7 Verify service
```bash
sudo systemctl status paper-trader
sudo journalctl -u paper-trader -f
```

Test locally:
```bash
curl http://localhost:8000/api/health
# {"status":"ok"}
```

## Step 3: Cloudflare Tunnel Setup

### 3.1 Install cloudflared
```bash
curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb
```

### 3.2 Authenticate with Cloudflare
```bash
cloudflared tunnel login
# Opens browser - select your domain
```

### 3.3 Create tunnel
```bash
cloudflared tunnel create paper-trader
# Output: Created tunnel paper-trader with id <TUNNEL_ID>
```

### 3.4 Configure tunnel
```bash
# Note the TUNNEL_ID from previous step
# Credentials file: ~/.cloudflared/<TUNNEL_ID>.json

mkdir -p ~/.cloudflared
cp /opt/paper-trader/deploy/cloudflared-config.yml ~/.cloudflared/config.yml
```

**Edit `~/.cloudflared/config.yml`:**
```yaml
tunnel: <TUNNEL_ID_FROM_STEP_3.3>
credentials-file: /home/youruser/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: trader.yourdomain.com
    service: http://localhost:8000
    originRequest:
      httpHostHeader: trader.yourdomain.com
      connectTimeout: 30s
      noTLSVerify: true
  - service: http_status:404
```

### 3.5 Route DNS
```bash
cloudflared tunnel route dns paper-trader trader.yourdomain.com
```

### 3.6 Install and start tunnel as service
```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
sudo systemctl status cloudflared
```

### 3.7 Verify tunnel
```bash
# Check tunnel status
cloudflared tunnel info paper-trader

# Test public URL
curl https://trader.yourdomain.com/api/health
# {"status":"ok"}
```

## Step 4: Verify Full Application

Test all endpoints:
```bash
# Health
curl https://trader.yourdomain.com/api/health

# Instruments
curl https://trader.yourdomain.com/api/instruments

# Option chain
curl https://trader.yourdomain.com/api/option-chain/NIFTY

# Positions
curl https://trader.yourdomain.com/api/positions

# Account
curl https://trader.yourdomain.com/api/account
```

Open in browser: `https://trader.yourdomain.com`

## Maintenance

### Update application
```bash
cd /opt/paper-trader
sudo -u paper-trader git pull
sudo -u paper-trader /opt/paper-trader/.venv/bin/pip install -r requirements.txt
sudo systemctl restart paper-trader
```

### View logs
```bash
# App logs
sudo journalctl -u paper-trader -f

# Tunnel logs
sudo journalctl -u cloudflared -f
```

### Backup database
```bash
sudo cp /opt/paper-trader/data/trader.db /opt/paper-trader/data/trader.db.backup.$(date +%F)
```

### Restart services
```bash
sudo systemctl restart paper-trader cloudflared
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Service won't start | Check `journalctl -u paper-trader -xe` |
| 502 Bad Gateway | Tunnel not running or wrong port in config.yml |
| 404 on API | Check ingress hostname matches your domain |
| Database locked | Ensure only one app instance runs |
| Angel One auth fails | Verify credentials in `/etc/paper-trader.env` |
| Static files not loading | Check `WEB_DIR` path in main.py matches deployment |

## Security Notes

- Never commit `.env` or credentials to Git
- Use `chmod 600` on `/etc/paper-trader.env`
- Run app as non-root user (`paper-trader`)
- Cloudflare provides TLS termination, WAF, DDoS protection
- Consider Cloudflare Access for additional authentication

## File Locations Summary

| File | Location |
|------|----------|
| App code | `/opt/paper-trader/` |
| Virtual env | `/opt/paper-trader/.venv/` |
| Database | `/opt/paper-trader/data/trader.db` |
| Logs | `/opt/paper-trader/logs/` + systemd journal |
| Env vars | `/etc/paper-trader.env` |
| Systemd service | `/etc/systemd/system/paper-trader.service` |
| Tunnel config | `~/.cloudflared/config.yml` |
| Tunnel credentials | `~/.cloudflared/<TUNNEL_ID>.json` |