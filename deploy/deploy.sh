#!/bin/bash
# deploy.sh — Quick setup script for Paper Trader on Linux VM
# Run as root or with sudo: sudo bash deploy.sh

set -euo pipefail

APP_USER="paper-trader"
APP_DIR="/opt/paper-trader"
REPO_URL="https://github.com/rey2507/stock-ai.git"
PYTHON_VERSION="3.13"

echo "=== Paper Trader Deployment Setup ==="
echo "This script sets up the app directory, user, Python venv, and systemd service."
echo "Run as root or with sudo."
echo ""

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root (use sudo)"
   exit 1
fi

# Create app user
if ! id "$APP_USER" &>/dev/null; then
    echo "Creating user: $APP_USER"
    useradd -r -s /bin/false -d "$APP_DIR" "$APP_USER"
else
    echo "User $APP_USER already exists"
fi

# Create app directory
echo "Creating app directory: $APP_DIR"
mkdir -p "$APP_DIR/data" "$APP_DIR/logs"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# Clone or update repository
if [[ -d "$APP_DIR/.git" ]]; then
    echo "Repository exists, pulling latest..."
    cd "$APP_DIR"
    sudo -u "$APP_USER" git pull
else
    echo "Cloning repository..."
    sudo -u "$APP_USER" git clone "$REPO_URL" "$APP_DIR"
fi

# Install Python if needed
if ! command -v "python$PYTHON_VERSION" &>/dev/null; then
    echo "Installing Python $PYTHON_VERSION..."
    apt-get update
    apt-get install -y "python$PYTHON_VERSION" "python$PYTHON_VERSION-venv" "python$PYTHON_VERSION-dev"
fi

# Create virtual environment
if [[ ! -d "$APP_DIR/.venv" ]]; then
    echo "Creating virtual environment..."
    sudo -u "$APP_USER" "python$PYTHON_VERSION" -m venv "$APP_DIR/.venv"
fi

# Install dependencies
echo "Installing Python dependencies..."
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# Install systemd service
echo "Installing systemd service..."
cp "$APP_DIR/deploy/paper-trader.service" /etc/systemd/system/paper-trader.service
systemctl daemon-reload

# Setup environment file
if [[ ! -f /etc/paper-trader.env ]]; then
    echo "Creating environment file template at /etc/paper-trader.env"
    cp "$APP_DIR/deploy/paper-trader.env" /etc/paper-trader.env
    chmod 600 /etc/paper-trader.env
    chown "$APP_USER:$APP_USER" /etc/paper-trader.env
    echo "⚠️  EDIT /etc/paper-trader.env with your Angel One credentials!"
else
    echo "Environment file already exists at /etc/paper-trader.env"
fi

# Initialize database
echo "Initializing database..."
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" -c "from backend.db import init_db; init_db()"

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "1. Edit /etc/paper-trader.env with your Angel One credentials"
echo "2. Start the service: systemctl enable --now paper-trader"
echo "3. Check status: systemctl status paper-trader"
echo "4. View logs: journalctl -u paper-trader -f"
echo ""
echo "Then set up Cloudflare Tunnel:"
echo "1. Install cloudflared (see deploy/cloudflared-config.yml)"
echo "2. Run: cloudflared tunnel login"
echo "3. Run: cloudflared tunnel create paper-trader"
echo "4. Update deploy/cloudflared-config.yml with tunnel ID and hostname"
echo "5. Run: cloudflared tunnel route dns paper-trader <your-hostname>"
echo "6. Install as service: cloudflared service install"
echo "7. Start: systemctl enable --now cloudflared"