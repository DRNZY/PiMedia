#!/bin/bash

if [[ $EUID -ne 0 ]]; then
   echo "Run as root: sudo ./clone.sh /dev/sdX"
   exit 1
fi

if [[ -z "$1" ]]; then
    echo "Usage: sudo ./clone.sh /dev/sdX"
    echo "Available devices:"
    lsblk -d | grep -E "sd|mmc"
    exit 1
fi

TARGET="$1"
HOSTNAME="pimedia$(date +%s | tail -c 4)"

echo "=== PiMedia SD Cloner ==="
echo "Source: Current SD"
echo "Target: $TARGET"
echo "New hostname: $HOSTNAME"
echo ""
echo "WARNING: Erases all data on $TARGET!"
echo "Continue? (yes/no)"
read -r confirm
[[ "$confirm" != "yes" ]] && exit 1

echo "[1/3] Reading SD..."
sudo dd if=/dev/mmcblk0 of=/tmp/pimedia.img bs=4M status=progress

echo "[2/3] Writing to new SD..."
sudo dd if=/tmp/pimedia.img of="$TARGET" bs=4M status=progress

echo "[3/3] Changing hostname..."
mkdir -p /tmp/piroot
sudo mount "${TARGET}2" /tmp/piroot
sudo sed -i "s/pimedia[0-9]*/$HOSTNAME/g" /tmp/piroot/etc/hostname
sudo sed -i "s/127.0.1.1.*/127.0.1.1\t$HOSTNAME/g" /tmp/piroot/etc/hosts
sudo umount /tmp/piroot

rm -f /tmp/pimedia.img

echo ""
echo "=== Clone Complete! ==="
echo "New hostname: $HOSTNAME"
echo "Insert in new Pi and boot"
echo "Run ./setup.sh on first boot"
