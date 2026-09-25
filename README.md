# PiMedia

Local-first media player and display appliance for Raspberry Pi and Linux desktops. Built to control `mpv` over an IPC UNIX socket with a modern web remote and REST API for projectors, exhibition displays, and home screens.

## Overview

- **Hardware playback**: Direct hardware-accelerated playback via `mpv` with gapless looping and fullscreen layer output.
- **IPC Remote**: Full bidirectional control over `/tmp/mpv-socket` (Play, Pause, Stop, Skip, Previous, Volume, and Fullscreen toggle).
- **Responsive Web Dashboard**: Touch-friendly remote control designed for mobile phone browsers and desktop viewports.
- **Media Management**: Drag-and-drop file uploader, media gallery filtering (Images vs Videos), instant single-file playback, and disk space telemetry.
- **Slideshow Engine**: Configurable image transition intervals (3s to 30s) alongside video playback.

## Architecture

```text
               +----------------------------------+
               |  Phone / Laptop Web Browser UI  |
               +-----------------+----------------+
                                 | HTTP / JSON API
                                 v
               +----------------------------------+
               |      PiMedia Flask Server        |
               |        (Port 5000)               |
               +-----------------+----------------+
                                 | UNIX IPC Socket
                                 v
               +----------------------------------+
               |         mpv Video Engine         |
               |   (HDMI Display / Framebuffer)   |
               +----------------------------------+
```

## Supported Formats

- **Video**: MP4, MOV, MKV, AVI, WebM
- **Images**: JPG, JPEG, PNG, GIF, WebP

## REST API Endpoints

- `GET /api/status`: Returns current playback state, now-playing file, volume, and host system vitals (disk usage, IP, CPU temperature).
- `GET /api/files`: Returns list of media items with sizes and types.
- `POST /api/playback/start`: Starts playlist playback (optional JSON payload: `{"file": "name.mp4", "duration": 10}`).
- `POST /api/playback/pause`: Toggles playback pause state.
- `POST /api/playback/stop`: Stops playback and terminates MPV instance.
- `POST /api/playback/next`: Advances to next item in playlist.
- `POST /api/playback/prev`: Returns to previous item in playlist.
- `POST /api/playback/volume`: Sets volume (`{"volume": 80}`).
- `POST /api/playback/duration`: Sets image slideshow duration in seconds (`{"duration": 15}`).
- `POST /api/playback/fullscreen`: Toggles fullscreen output.
- `POST /api/upload`: Multipart file upload endpoint.
- `POST /api/delete`: Deletes file (`{"filename": "photo.jpg"}`).

## Installation

```bash
# 1. Clone repository
git clone https://github.com/DRNZY/PiMedia.git
cd PiMedia

# 2. Run automated installer (installs mpv, python3-flask, systemd service)
chmod +x install.sh setup.sh
./install.sh

# 3. Access web dashboard from your phone or browser
http://<raspberry-pi-ip>:5000
```

## Systemd Service

PiMedia runs as a systemd background service:

```bash
# Check status
systemctl status media

# Restart service
sudo systemctl restart media
```

## License

MIT License. Copyright (c) 2026 Darnell Dijksteel.
