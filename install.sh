#!/bin/bash
set -e

echo "=== PiMedia Installer ==="
echo "Installing dependencies..."

sudo apt update -qq
sudo apt install -y -qq mpv python3-flask python3-pip socat samba hostapd dnsmasq qrencode 2>&1 | grep -v "^Selecting\|^Preparing\|^Unpacking\|^Setting up\|^Processing"

echo "Creating media directory..."
mkdir -p /home/pi/media
chmod 755 /home/pi/media

echo "Copying files..."
cp "$(dirname "$0")/server.py" /home/pi/server.py
cp "$(dirname "$0")/slideshow.sh" /home/pi/slideshow.sh
chmod +x /home/pi/slideshow.sh

echo "Creating systemd service..."
sudo tee /etc/systemd/system/media.service > /dev/null << 'EOF'
[Unit]
Description=PiMedia Server
After=network.target
Wants=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /home/pi/server.py
WorkingDirectory=/home/pi
Restart=always
RestartSec=3
User=pi
Environment=DISPLAY=:0
Environment=HOME=/home/pi

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable media --now

echo "Testing installation..."
sleep 2
if systemctl is-active --quiet media; then
    IP=$(hostname -I | awk '{print $1}')
    echo ""
    echo "=== Installation Complete! ==="
    echo "Web interface: http://${IP}:5000"
    echo "Media folder:  /home/pi/media"
    echo "Run ./setup.sh to configure WiFi and hostname"
else
    echo "Service failed. Check: sudo systemctl status media"
    exit 1
fi
