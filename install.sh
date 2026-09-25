#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_USER="${SUDO_USER:-$USER}"
USER_HOME=$(eval echo "~$CURRENT_USER")

echo "========================================="
echo "       PiMedia Appliance Installer       "
echo "========================================="

echo "[1/4] Installing system dependencies..."
if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq mpv python3 python3-venv python3-pip network-manager socat qrencode libnotify-bin 2>/dev/null || true
elif command -v pacman &>/dev/null; then
    sudo pacman -Sy --noconfirm --needed mpv python python-virtualenv networkmanager socat qrencode 2>/dev/null || true
fi

echo "[2/4] Setting up Python virtual environment..."
if [ ! -d "$DIR/.venv" ]; then
    python3 -m venv "$DIR/.venv"
fi
"$DIR/.venv/bin/pip" install --quiet flask werkzeug

echo "[3/4] Initializing default media library..."
MEDIA_DIR="$USER_HOME/media"
mkdir -p "$MEDIA_DIR"
chown -R "$CURRENT_USER:$CURRENT_USER" "$MEDIA_DIR" 2>/dev/null || true

echo "[4/4] Configuring systemd service..."
sudo tee /etc/systemd/system/pimedia.service > /dev/null << EOF
[Unit]
Description=PiMedia Appliance Server
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python $DIR/server.py
Restart=always
RestartSec=3
Environment=DISPLAY=:0
Environment=HOME=$USER_HOME
Environment=PIMEDIA_DIR=$MEDIA_DIR

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable pimedia --now 2>/dev/null || true

IP=$(hostname -I 2>/dev/null | awk "{print \$1}")
IP=${IP:-127.0.0.1}

echo ""
echo "========================================="
echo "       Installation Successful!          "
echo "========================================="
echo "Web Remote URL: http://${IP}:5000"
echo "Media Directory: $MEDIA_DIR"
echo "CLI Utility:     $DIR/pimedia.sh"
echo ""
if command -v qrencode &>/dev/null; then
    echo "Scan QR code on your phone to open remote:"
    qrencode -t ANSI "http://${IP}:5000" 2>/dev/null || true
fi
