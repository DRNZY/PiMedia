#!/usr/bin/env bash
set -e

echo "PiMedia Setup"

read -rp "Hostname [pimedia]: " hostname
hostname=${hostname:-pimedia}
sudo hostnamectl set-hostname "$hostname"
sudo sed -i "s/127.0.1.1.*/127.0.1.1\t$hostname/" /etc/hosts 2>/dev/null || true

read -rp "Wi-Fi SSID (leave empty to skip): " ssid
if [[ -n "$ssid" ]]; then
    read -rsp "Wi-Fi Password: " password
    echo ""
    if command -v nmcli &>/dev/null; then
        nmcli dev wifi connect "$ssid" password "$password"
    elif command -v wpa_passphrase &>/dev/null; then
        wpa_passphrase "$ssid" "$password" | sudo tee -a /etc/wpa_supplicant/wpa_supplicant.conf > /dev/null
        sudo wpa_cli -i wlan0 reconfigure 2>/dev/null || true
    fi
fi

if command -v raspi-config &>/dev/null; then
    echo "Display resolution:"
    echo "1) Auto-detect"
    echo "2) 1080p (1920x1080)"
    echo "3) 720p (1280x720)"
    echo "4) 4K (3840x2160)"
    read -rp "Choice [1]: " res
    case "$res" in
        2) sudo raspi-config nonint do_resolution 2 82 2>/dev/null || true ;;
        3) sudo raspi-config nonint do_resolution 2 85 2>/dev/null || true ;;
        4) sudo raspi-config nonint do_resolution 2 95 2>/dev/null || true ;;
    esac
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
IP=${IP:-127.0.0.1}

echo "Setup complete. Web interface available at http://${IP}:5000"
