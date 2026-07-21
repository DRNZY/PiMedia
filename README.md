# PiMedia - Raspberry Pi Media Player

## Nederlands

### Wat is dit?
Een simpel mediasysteem voor Raspberry Pi. Upload foto's en video's via je telefoon, speel ze af op een beamer of TV.

### Benodigdheden
- Raspberry Pi 4 (2GB+)
- Micro SD kaart (16GB+)
- Voeding (USB-C 3A)
- HDMI kabel
- Beamer of TV

### Installatie (5 minuten)

Stap 1: Flash Raspberry Pi OS Lite op SD kaart met Raspberry Pi Imager

Stap 2: Kopieer deze files naar de Pi:
    scp -r . pi@pimedia.local:~/pimedia/
    ssh pi@pimedia.local
    cd ~/pimedia
    chmod +x install.sh setup.sh

Stap 3: Installeer:
    ./install.sh

Stap 4: Configureer:
    ./setup.sh

Stap 5: Open telefoon browser, ga naar http://[IP]:5000

### Gebruik
- Upload: Sleep bestanden in web interface
- Afspelen: Klik "Start" op telefoon
- Volgende: Klik "Volgende" om te skippen
- Stop: Klik "Stop"

### Bestandsformaten
- Afbeeldingen: JPG, PNG, GIF
- Video's: MP4, MOV, AVI, MKV

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
