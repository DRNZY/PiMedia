# PiMedia

PiMedia is a local media player and display controller for Raspberry Pi and Linux desktops. It runs `mpv` via a UNIX IPC socket and exposes a web remote and REST API for projectors, exhibition displays, and screens.

## Features

- Hardware-accelerated video and photo playback through mpv
- Web remote for phone and desktop browsers
- Drag-and-drop file uploader with media library management
- URL video streaming for YouTube, Vimeo, and direct streams via yt-dlp
- Photo slideshows with configurable duration and optional pan and zoom drift
- Background audio loop during photo slideshows
- Wireless network scanning and connection directly from the browser
- Automatic hotspot fallback (`PiMedia-Setup`) when no network is reachable
- Timed display sleep and wake scheduling
- Optional PIN protection for public setups
- HDMI-CEC TV remote support

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
               |            mpv Engine            |
               |   (HDMI Display / Framebuffer)   |
               +----------------------------------+
```

## Supported formats

- Video: MP4, MOV, MKV, AVI, WebM
- Images: JPG, JPEG, PNG, GIF, WebP, SVG
- Audio: MP3, FLAC, WAV, OGG, M4A, AAC

## Installation

```bash
git clone https://github.com/DRNZY/PiMedia.git
cd PiMedia
chmod +x install.sh pimedia.sh
./install.sh
```

The installer installs dependencies (`mpv`, `python3`, `network-manager`), creates a virtual environment, and enables the `pimedia` systemd service.

Open `http://<device-ip>:5000` in a browser on the same network to access the remote.

## Command-line utility

The `pimedia.sh` script provides quick control over the server:

```bash
./pimedia.sh run           # Run server in the foreground
./pimedia.sh start         # Start background service
./pimedia.sh stop          # Stop server and active playback
./pimedia.sh status        # Show process status
./pimedia.sh wifi-scan     # Scan available wireless networks
./pimedia.sh wifi-connect  # Connect to a wireless network
```

## Systemd service

Manage the background service with systemctl:

```bash
sudo systemctl status pimedia
sudo systemctl restart pimedia
sudo systemctl stop pimedia
```

## REST API

- `GET /api/status`: Returns current playback state, now-playing file, volume, disk usage, IP address, and CPU temperature.
- `GET /api/files`: Returns list of media items in the library.
- `POST /api/playback/start`: Starts playback (`{"file": "sample.mp4", "duration": 10}`).
- `POST /api/playback/stream`: Streams a URL (`{"url": "https://..."}`).
- `POST /api/playback/pause`: Toggles playback pause.
- `POST /api/playback/stop`: Stops playback.
- `POST /api/playback/next`: Advances to next item.
- `POST /api/playback/prev`: Returns to previous item.
- `POST /api/playback/volume`: Sets volume (`{"volume": 80}`).
- `POST /api/playback/duration`: Sets image display duration in seconds (`{"duration": 15}`).
- `POST /api/playback/kenburns`: Toggles pan and zoom drift (`{"enabled": true}`).
- `POST /api/playback/background_audio`: Sets background audio track (`{"audio_file": "ambient.mp3"}`).
- `POST /api/playback/fullscreen`: Toggles fullscreen output.
- `POST /api/display/power`: Toggles display power (`{"power": true}`).
- `POST /api/display/schedule`: Sets sleep and wake times (`{"enabled": true, "sleep_time": "23:00", "wake_time": "07:00"}`).
- `POST /api/wifi/scan`: Scans for nearby Wi-Fi networks.
- `POST /api/wifi/connect`: Connects to a Wi-Fi network (`{"ssid": "Network", "password": "pass"}`).
- `POST /api/settings/pin`: Configures an access PIN (`{"pin": "1234"}`).
- `POST /api/upload`: Multipart file upload endpoint.
- `POST /api/delete`: Deletes a file (`{"filename": "photo.jpg"}`).

## License

MIT License. Copyright (c) 2026 Darnell Dijksteel.
