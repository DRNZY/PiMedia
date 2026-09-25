#!/usr/bin/env bash
# PiMedia Automatic Hotspot Fallback
# If no known Wi-Fi network connects within 20s of boot, launches setup AP.

HOTSPOT_SSID="PiMedia-Setup"
HOTSPOT_PASS="pimedia123"

sleep 15

# Check if we have a valid default route / IP
if ip route | grep -q default; then
    echo "Network connection established. Fallback hotspot not required."
    exit 0
fi

echo "No network connection detected. Initiating fallback setup hotspot..."

if command -v nmcli &>/dev/null; then
    # Create or activate temporary Wi-Fi hotspot using NetworkManager
    nmcli connection delete "$HOTSPOT_SSID" 2>/dev/null || true
    nmcli dev wifi hotspot ifname wlan0 ssid "$HOTSPOT_SSID" password "$HOTSPOT_PASS" 2>/dev/null || true
    echo "Fallback Hotspot Active: SSID='$HOTSPOT_SSID' PASS='$HOTSPOT_PASS'"
    echo "Connect to $HOTSPOT_SSID on your phone and open http://192.168.4.1:5000"
fi
