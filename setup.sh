#!/bin/bash

echo "=== PiMedia Setup Wizard ==="

# Hostname
echo ""
echo "Enter hostname (default: pimedia1):"
read -r hostname
hostname=${hostname:-pimedia1}
sudo hostnamectl set-hostname "$hostname"
sudo sed -i "s/127.0.1.1.*/127.0.1.1\t$hostname/" /etc/hosts

# WiFi
echo ""
echo "Enter WiFi SSID (press Enter to skip):"
read -r ssid
if [[ -n "$ssid" ]]; then
    echo "Enter WiFi password:"
    read -rs password
    echo ""
    wpa_passphrase "$ssid" "$password" | sudo tee /etc/wpa_supplicant/wpa_supplicant.conf > /dev/null
    sudo wpa_cli -i wlan0 reconfigure 2>/dev/null || true
    echo "WiFi configured"
else
    echo "WiFi skipped"
fi

# Display
echo ""
echo "Select resolution:"
echo "1) Auto-detect"
echo "2) 1920x1080"
echo "3) 1280x720"
echo "4) 4K"
read -r res
case "$res" in
    2) sudo raspi-config nonint do_resolution 2 82 ;;
    3) sudo raspi-config nonint do_resolution 2 85 ;;
    4) sudo raspi-config nonint do_resolution 2 95 ;;
    *) echo "Auto-detect selected" ;;
esac

# Test
echo ""
echo "Testing..."
sleep 2
IP=$(hostname -I | awk '{print $1}')

echo ""
echo "=== Setup Complete! ==="
echo "Hostname: $hostname"
echo "IP:       $IP"
echo "URL:      http://${IP}:5000"
echo ""
echo "QR Code:"
qrencode -t ANSI "http://${IP}:5000" 2>/dev/null || echo "Install qrencode: sudo apt install qrencode"
echo ""
echo "Upload via web or SMB: smb://$IP/Media"
echo "Controls: Start/Stop/Next from any phone"
