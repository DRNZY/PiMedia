#!/usr/bin/env bash
# PiMedia Wi-Fi Fallback Hotspot
# Starts a local setup hotspot if no network connects within 15 seconds of boot.

HOTSPOT_SSID="PiMedia-Setup"
HOTSPOT_PASS="pimedia123"

sleep 15

if ip route | grep -q default; then
    exit 0
fi

echo "No network detected. Starting setup hotspot..."

if command -v nmcli &>/dev/null; then
    nmcli connection delete "$HOTSPOT_SSID" 2>/dev/null || true
    nmcli dev wifi hotspot ifname wlan0 ssid "$HOTSPOT_SSID" password "$HOTSPOT_PASS" 2>/dev/null || true
    echo "Hotspot active: SSID '$HOTSPOT_SSID', Password '$HOTSPOT_PASS'"
    echo "Web interface: http://192.168.4.1:5000"
fi
