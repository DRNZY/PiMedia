# PiMedia - Raspberry Pi Media Player

## English

### What is this?
A simple media system for Raspberry Pi. Upload photos and videos from your phone, play them on a projector or TV.

### Requirements
- Raspberry Pi 4 (2GB+)
- Micro SD card (16GB+)
- Power supply (USB-C 3A)
- HDMI cable
- Projector or TV

### Installation (5 minutes)

Step 1: Flash Raspberry Pi OS Lite to SD card with Raspberry Pi Imager

Step 2: Copy these files to the Pi:
    scp -r . pi@pimedia.local:~/pimedia/
    ssh pi@pimedia.local
    cd ~/pimedia
    chmod +x install.sh setup.sh

Step 3: Install:
    ./install.sh

Step 4: Configure:
    ./setup.sh

Step 5: Open phone browser, go to http://[IP]:5000

### Usage
- Upload: Drag files in web interface
- Play: Click "Start" on phone
- Next: Click "Next" to skip
- Stop: Click "Stop"

### File Formats
- Images: JPG, PNG, GIF
- Videos: MP4, MOV, AVI, MKV

## Troubleshooting

Problem: Can't access web interface
Solution: Check IP with hostname -I

Problem: Black screen on projector
Solution: Check HDMI cable, reboot

Problem: Videos don't play
Solution: Install ffmpeg with sudo apt install ffmpeg

Problem: Upload fails
Solution: Check disk space with df -h

License: MIT - Free for personal and commercial use.
