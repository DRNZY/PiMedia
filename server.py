#!/usr/bin/env python3
"""
PiMedia - Precision Local Media Appliance for Displays, Projectors & Linux
Features Apple Human Interface Design principles, MPV UNIX IPC control,
and seamless zero-config portable Wi-Fi networking.
"""

import os
import sys
import json
import time
import socket
import shutil
import subprocess
import re
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, render_template_string
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Base configuration
DEFAULT_MEDIA = os.environ.get("PIMEDIA_DIR")
if not DEFAULT_MEDIA:
    if os.path.exists("/home/pi") and os.access("/home/pi", os.W_OK):
        DEFAULT_MEDIA = "/home/pi/media"
    else:
        DEFAULT_MEDIA = os.path.expanduser("~/media")

MEDIA_DIR = DEFAULT_MEDIA
SOCKET_PATH = "/tmp/mpv-socket"
PID_PATH = "/tmp/mpv.pid"
PLAYLIST_PATH = "/tmp/pimedia-playlist.m3u"
CONFIG_PATH = os.path.expanduser("~/.pimedia-config.json")
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "svg", "mp4", "mov", "avi", "mkv", "webm"}
MAX_CONTENT_LENGTH = 1024 * 1024 * 1024  # 1 GB cap
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"auth_pin": "", "image_duration": 10, "auto_loop": True}

def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as err:
        print(f"Failed to save config: {err}", file=sys.stderr)

CONFIG = load_config()

def require_auth():
    """Verify PIN/token if configured."""
    pin = CONFIG.get("auth_pin", "").strip()
    if not pin:
        return None
    client_auth = request.headers.get("X-Auth-Token") or request.args.get("token") or request.cookies.get("pimedia_auth")
    if client_auth != pin:
        return jsonify({"error": "Unauthorized. PIN required."}), 401
    return None

def get_media_dir():
    os.makedirs(MEDIA_DIR, exist_ok=True)
    return MEDIA_DIR

def is_allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def get_file_type(ext):
    ext = ext.lower()
    if ext in {"mp4", "mov", "avi", "mkv", "webm"}:
        return "video"
    return "image"

def list_media_files():
    folder = get_media_dir()
    items = []
    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return []

    for name in entries:
        if name.startswith("."):
            continue
        ext = name.rsplit(".", 1)[1].lower() if "." in name else ""
        if ext in ALLOWED_EXTENSIONS:
            full_path = os.path.join(folder, name)
            try:
                stat = os.stat(full_path)
                items.append({
                    "name": name,
                    "type": get_file_type(ext),
                    "extension": ext,
                    "size": stat.st_size,
                    "modified": int(stat.st_mtime),
                })
            except OSError:
                continue
    return items

def send_mpv_command(cmd, timeout=3.0):
    """Send JSON IPC command to running MPV instance over UNIX socket."""
    if not os.path.exists(SOCKET_PATH):
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(SOCKET_PATH)
            payload = json.dumps(cmd) + "\n"
            sock.sendall(payload.encode("utf-8"))
            data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in chunk:
                    break
            if not data:
                return None
            return json.loads(data.decode("utf-8"))
    except (socket.error, json.JSONDecodeError, OSError):
        return None

def is_mpv_alive():
    if not os.path.exists(PID_PATH):
        return False
    try:
        with open(PID_PATH, "r") as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        res = send_mpv_command({"command": ["get_property", "idle-active"]}, timeout=1.0)
        return res is not None
    except (ValueError, OSError):
        return False

def terminate_mpv():
    if os.path.exists(SOCKET_PATH):
        send_mpv_command({"command": ["quit"]}, timeout=1.0)

    try:
        if os.path.exists(PID_PATH):
            with open(PID_PATH, "r") as f:
                pid = int(f.read().strip())
            os.kill(pid, 15)
            time.sleep(0.3)
            os.kill(pid, 9)
    except (ValueError, OSError):
        pass

    subprocess.run(["pkill", "-f", "mpv --input-ipc-server=" + SOCKET_PATH], stderr=subprocess.DEVNULL)

    for path in [PID_PATH, PLAYLIST_PATH, SOCKET_PATH]:
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

def start_mpv_playback(target_file=None, image_duration=None):
    terminate_mpv()
    folder = get_media_dir()
    files = list_media_files()
    if not files:
        return False, "No media files found in library"

    if image_duration is None:
        image_duration = CONFIG.get("image_duration", 10)

    playlist_items = []
    if target_file:
        match = next((f for f in files if f["name"] == target_file), None)
        if match:
            playlist_items.append(os.path.join(folder, match["name"]))

    for f in files:
        full = os.path.join(folder, f["name"])
        if full not in playlist_items:
            playlist_items.append(full)

    try:
        with open(PLAYLIST_PATH, "w", encoding="utf-8") as pf:
            for p in playlist_items:
                pf.write(p + "\n")
    except OSError as err:
        return False, f"Failed to write playlist: {err}"

    env = os.environ.copy()
    if "DISPLAY" not in env:
        env["DISPLAY"] = ":0"

    cmd = [
        "mpv",
        "--fs",
        "--loop-playlist=yes",
        f"--playlist={PLAYLIST_PATH}",
        f"--input-ipc-server={SOCKET_PATH}",
        f"--image-display-duration={int(image_duration)}",
        "--no-osc",
        "--no-osd-bar",
        "--quiet",
        "--keep-open=yes",
    ]

    try:
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(PID_PATH, "w", encoding="utf-8") as f:
            f.write(str(proc.pid))
        time.sleep(0.5)
        return True, "Playback started"
    except Exception as err:
        return False, f"Failed to spawn mpv: {err}"

def get_system_telemetry():
    folder = get_media_dir()
    disk_total, disk_used, disk_free = shutil.disk_usage(folder)

    ip_addr = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip_addr = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    cpu_temp = None
    try:
        if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                cpu_temp = round(int(f.read().strip()) / 1000.0, 1)
    except Exception:
        pass

    return {
        "ip": ip_addr,
        "disk_free_gb": round(disk_free / (1024**3), 2),
        "disk_total_gb": round(disk_total / (1024**3), 2),
        "disk_used_percent": round((disk_used / disk_total) * 100, 1),
        "cpu_temp": cpu_temp,
        "media_count": len(list_media_files()),
        "pin_active": bool(CONFIG.get("auth_pin")),
    }

# ==========================================
# Wi-Fi Management Helpers (NetworkManager/wpa)
# ==========================================

def scan_wifi_networks():
    """Scan available Wi-Fi networks using nmcli or iwlist."""
    networks = []
    seen = set()

    # Try nmcli first (Standard on Raspberry Pi OS Bookworm & Modern Linux)
    if shutil.which("nmcli"):
        try:
            res = subprocess.run(
                ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE", "dev", "wifi", "list", "--rescan", "auto"],
                capture_output=True, text=True, timeout=8
            )
            for line in res.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split(":")
                if len(parts) >= 3:
                    ssid = parts[0].strip()
                    if not ssid or ssid == "--" or ssid in seen:
                        continue
                    seen.add(ssid)
                    signal = int(parts[1]) if parts[1].isdigit() else 50
                    sec = parts[2].strip() or "Open"
                    in_use = (parts[3].strip() == "*") if len(parts) >= 4 else False
                    networks.append({
                        "ssid": ssid,
                        "signal": signal,
                        "security": sec,
                        "connected": in_use
                    })
            if networks:
                return sorted(networks, key=lambda x: (not x["connected"], -x["signal"]))
        except Exception:
            pass

    return networks

def connect_to_wifi(ssid, password):
    """Connect to a Wi-Fi network using NetworkManager."""
    if not ssid:
        return False, "SSID is required"

    if shutil.which("nmcli"):
        try:
            cmd = ["nmcli", "dev", "wifi", "connect", ssid]
            if password:
                cmd.extend(["password", password])
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if res.returncode == 0:
                return True, f"Connected to {ssid}"
            return False, res.stderr.strip() or "Connection failed"
        except Exception as err:
            return False, f"NetworkManager error: {err}"

    # Fallback to wpa_cli if nmcli is absent
    if shutil.which("wpa_cli"):
        try:
            p = subprocess.run(["wpa_passphrase", ssid, password or ""], capture_output=True, text=True)
            if p.returncode == 0:
                with open("/etc/wpa_supplicant/wpa_supplicant.conf", "a") as f:
                    f.write("\n" + p.stdout)
                subprocess.run(["wpa_cli", "-i", "wlan0", "reconfigure"], check=False)
                return True, f"Configured {ssid}"
        except Exception as err:
            return False, f"wpa_cli error: {err}"

    return False, "No supported Wi-Fi management utility found (nmcli or wpa_cli required)"

# ==========================================
# REST API Endpoints
# ==========================================

@app.route("/api/status", methods=["GET"])
def api_status():
    auth_res = require_auth()
    if auth_res:
        return auth_res

    running = is_mpv_alive()
    playback = {
        "is_running": running,
        "filename": None,
        "paused": False,
        "duration": 0,
        "position": 0,
        "volume": 100,
        "fullscreen": True,
        "image_duration": CONFIG.get("image_duration", 10),
    }

    if running:
        path_res = send_mpv_command({"command": ["get_property", "path"]})
        if path_res and "data" in path_res and path_res["data"]:
            playback["filename"] = os.path.basename(path_res["data"])

        pause_res = send_mpv_command({"command": ["get_property", "pause"]})
        if pause_res and "data" in pause_res:
            playback["paused"] = bool(pause_res["data"])

        dur_res = send_mpv_command({"command": ["get_property", "duration"]})
        if dur_res and "data" in dur_res and dur_res["data"] is not None:
            playback["duration"] = round(float(dur_res["data"]), 1)

        pos_res = send_mpv_command({"command": ["get_property", "time-pos"]})
        if pos_res and "data" in pos_res and pos_res["data"] is not None:
            playback["position"] = round(float(pos_res["data"]), 1)

        vol_res = send_mpv_command({"command": ["get_property", "volume"]})
        if vol_res and "data" in vol_res and vol_res["data"] is not None:
            playback["volume"] = int(vol_res["data"])

    return jsonify({
        "playback": playback,
        "system": get_system_telemetry(),
        "config": {
            "image_duration": CONFIG.get("image_duration", 10),
            "pin_configured": bool(CONFIG.get("auth_pin")),
        }
    })

@app.route("/api/files", methods=["GET"])
def api_files():
    auth_res = require_auth()
    if auth_res:
        return auth_res
    return jsonify(list_media_files())

@app.route("/api/playback/start", methods=["POST"])
def api_playback_start():
    auth_res = require_auth()
    if auth_res:
        return auth_res

    data = request.get_json(silent=True) or {}
    target = data.get("file")
    duration = data.get("duration", CONFIG.get("image_duration", 10))

    ok, msg = start_mpv_playback(target_file=target, image_duration=duration)
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"message": msg})

@app.route("/api/playback/stop", methods=["POST"])
def api_playback_stop():
    auth_res = require_auth()
    if auth_res:
        return auth_res
    terminate_mpv()
    return jsonify({"message": "Playback stopped"})

@app.route("/api/playback/pause", methods=["POST"])
def api_playback_pause():
    auth_res = require_auth()
    if auth_res:
        return auth_res
    res = send_mpv_command({"command": ["cycle", "pause"]})
    if res is None:
        return jsonify({"error": "MPV not connected"}), 503
    return jsonify({"message": "Toggled pause"})

@app.route("/api/playback/next", methods=["POST"])
def api_playback_next():
    auth_res = require_auth()
    if auth_res:
        return auth_res
    res = send_mpv_command({"command": ["playlist-next"]})
    if res is None:
        return jsonify({"error": "MPV not connected"}), 503
    return jsonify({"message": "Next track"})

@app.route("/api/playback/prev", methods=["POST"])
def api_playback_prev():
    auth_res = require_auth()
    if auth_res:
        return auth_res
    res = send_mpv_command({"command": ["playlist-prev"]})
    if res is None:
        return jsonify({"error": "MPV not connected"}), 503
    return jsonify({"message": "Previous track"})

@app.route("/api/playback/volume", methods=["POST"])
def api_playback_volume():
    auth_res = require_auth()
    if auth_res:
        return auth_res
    data = request.get_json(silent=True) or {}
    vol = max(0, min(100, int(data.get("volume", 100))))
    res = send_mpv_command({"command": ["set_property", "volume", vol]})
    if res is None:
        return jsonify({"error": "MPV not connected"}), 503
    return jsonify({"message": f"Volume set to {vol}%", "volume": vol})

@app.route("/api/playback/duration", methods=["POST"])
def api_playback_duration():
    auth_res = require_auth()
    if auth_res:
        return auth_res
    data = request.get_json(silent=True) or {}
    duration = max(2, min(300, int(data.get("duration", 10))))
    CONFIG["image_duration"] = duration
    save_config(CONFIG)
    send_mpv_command({"command": ["set_property", "image-display-duration", duration]})
    return jsonify({"message": f"Slideshow duration set to {duration}s", "duration": duration})

@app.route("/api/playback/fullscreen", methods=["POST"])
def api_playback_fullscreen():
    auth_res = require_auth()
    if auth_res:
        return auth_res
    res = send_mpv_command({"command": ["cycle", "fullscreen"]})
    if res is None:
        return jsonify({"error": "MPV not connected"}), 503
    return jsonify({"message": "Toggled fullscreen"})

@app.route("/api/wifi/scan", methods=["GET"])
def api_wifi_scan():
    auth_res = require_auth()
    if auth_res:
        return auth_res
    nets = scan_wifi_networks()
    return jsonify(nets)

@app.route("/api/wifi/connect", methods=["POST"])
def api_wifi_connect():
    auth_res = require_auth()
    if auth_res:
        return auth_res
    data = request.get_json(silent=True) or {}
    ssid = data.get("ssid", "").strip()
    pwd = data.get("password", "").strip()
    ok, msg = connect_to_wifi(ssid, pwd)
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"message": msg})

@app.route("/api/settings/pin", methods=["POST"])
def api_settings_pin():
    auth_res = require_auth()
    if auth_res:
        return auth_res
    data = request.get_json(silent=True) or {}
    new_pin = data.get("pin", "").strip()
    CONFIG["auth_pin"] = new_pin
    save_config(CONFIG)
    return jsonify({"message": "Security PIN updated", "pin_configured": bool(new_pin)})

@app.route("/api/upload", methods=["POST"])
def api_upload():
    auth_res = require_auth()
    if auth_res:
        return auth_res

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400

    if not is_allowed_file(f.filename):
        return jsonify({"error": "Unsupported media format"}), 400

    filename = secure_filename(f.filename)
    if not filename:
        return jsonify({"error": "Invalid filename"}), 400

    folder = get_media_dir()
    dest = os.path.join(folder, filename)

    if os.path.exists(dest):
        stem, ext = os.path.splitext(filename)
        filename = f"{stem}_{int(time.time())}{ext}"
        dest = os.path.join(folder, filename)

    try:
        f.save(dest)
    except Exception as err:
        return jsonify({"error": f"Failed to save file: {err}"}), 500

    return jsonify({"message": "Uploaded successfully", "filename": filename})

@app.route("/api/delete", methods=["POST"])
def api_delete():
    auth_res = require_auth()
    if auth_res:
        return auth_res

    data = request.get_json(silent=True) or {}
    filename = secure_filename(data.get("filename", ""))
    if not filename:
        return jsonify({"error": "Invalid filename"}), 400

    folder = get_media_dir()
    target = os.path.join(folder, filename)
    if os.path.exists(target):
        try:
            os.remove(target)
            return jsonify({"message": f"Deleted {filename}"})
        except OSError as err:
            return jsonify({"error": f"Deletion failed: {err}"}), 500

    return jsonify({"error": "File not found"}), 404

@app.route("/media/<path:filename>")
def serve_media(filename):
    return send_from_directory(get_media_dir(), filename)

# ==========================================
# Apple Design System Web UI
# ==========================================

INDEX_HTML = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover" />
  <title>PiMedia &mdash; Control Center</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    :root {
      --apple-ease: cubic-bezier(0.16, 1, 0.3, 1);
    }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Inter", sans-serif;
      background-color: #000000;
      color: #f5f5f7;
      letter-spacing: -0.011em;
    }
    .apple-glass {
      background: rgba(18, 18, 20, 0.75);
      backdrop-filter: blur(40px) saturate(190%);
      -webkit-backdrop-filter: blur(40px) saturate(190%);
      border: 1px solid rgba(255, 255, 255, 0.08);
      box-shadow: 0 30px 60px -15px rgba(0, 0, 0, 0.8), 0 0 1px 1px rgba(255, 255, 255, 0.05);
    }
    .apple-card {
      background: rgba(24, 24, 27, 0.65);
      backdrop-filter: blur(30px);
      -webkit-backdrop-filter: blur(30px);
      border: 1px solid rgba(255, 255, 255, 0.06);
      transition: all 0.35s var(--apple-ease);
    }
    .apple-card:hover {
      border-color: rgba(255, 255, 255, 0.14);
      transform: translateY(-2px);
    }
    .apple-pill {
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.08);
      transition: all 0.25s var(--apple-ease);
    }
    .apple-pill:hover {
      background: rgba(255, 255, 255, 0.14);
      border-color: rgba(255, 255, 255, 0.18);
    }
    .apple-btn-primary {
      background: #0071e3;
      transition: all 0.25s var(--apple-ease);
      box-shadow: 0 8px 20px -4px rgba(0, 113, 227, 0.4);
    }
    .apple-btn-primary:hover {
      background: #0077ed;
      transform: scale(1.02);
    }
    .apple-btn-primary:active {
      transform: scale(0.98);
    }
    .apple-btn-danger {
      background: rgba(255, 69, 58, 0.15);
      color: #ff453a;
      border: 1px solid rgba(255, 69, 58, 0.3);
      transition: all 0.25s var(--apple-ease);
    }
    .apple-btn-danger:hover {
      background: rgba(255, 69, 58, 0.25);
      border-color: rgba(255, 69, 58, 0.5);
    }
    .drop-active {
      border-color: #0071e3 !important;
      background-color: rgba(0, 113, 227, 0.08) !important;
    }
    /* Smooth custom slider */
    input[type=range] {
      -webkit-appearance: none;
      background: rgba(255, 255, 255, 0.15);
      border-radius: 9999px;
      height: 4px;
    }
    input[type=range]::-webkit-slider-thumb {
      -webkit-appearance: none;
      width: 14px;
      height: 14px;
      border-radius: 50%;
      background: #ffffff;
      box-shadow: 0 2px 6px rgba(0, 0, 0, 0.4);
      cursor: pointer;
      transition: transform 0.15s ease;
    }
    input[type=range]::-webkit-slider-thumb:hover {
      transform: scale(1.2);
    }
  </style>
</head>
<body class="min-h-screen flex flex-col antialiased selection:bg-blue-600 selection:text-white pb-12">

  <!-- Apple Studio Ambient Light Gradient -->
  <div class="fixed top-0 left-1/2 -translate-x-1/2 w-[800px] h-[320px] bg-gradient-to-b from-blue-600/10 via-zinc-900/0 to-transparent blur-3xl pointer-events-none -z-10"></div>

  <!-- Top Navigation Bar -->
  <header class="sticky top-0 z-40 px-4 py-3 sm:px-8 border-b border-white/5 bg-black/70 backdrop-blur-2xl">
    <div class="max-w-6xl mx-auto flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-xl bg-gradient-to-br from-blue-500 to-blue-700 flex items-center justify-center shadow-lg shadow-blue-500/20">
          <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
        </div>
        <div>
          <h1 class="text-sm font-semibold tracking-tight text-white leading-none">PiMedia</h1>
          <p class="text-[11px] text-zinc-400 font-mono mt-0.5" id="hostIp">Connecting...</p>
        </div>
      </div>

      <!-- Segmented View Tabs (Now Playing, Media, Wi-Fi & Settings) -->
      <div class="hidden sm:flex items-center bg-zinc-900/80 p-1 rounded-full border border-white/5 text-xs">
        <button onclick="switchView('remote')" id="tabViewRemote" class="px-4 py-1.5 rounded-full bg-zinc-800 text-white font-medium transition shadow-sm">Remote</button>
        <button onclick="switchView('media')" id="tabViewMedia" class="px-4 py-1.5 rounded-full text-zinc-400 hover:text-white transition">Media</button>
        <button onclick="switchView('settings')" id="tabViewSettings" class="px-4 py-1.5 rounded-full text-zinc-400 hover:text-white transition">Wi-Fi &amp; Settings</button>
      </div>

      <div class="flex items-center gap-2">
        <div id="statusBadge" class="flex items-center gap-2 px-3 py-1 rounded-full text-xs font-medium bg-zinc-900 text-zinc-400 border border-white/5">
          <span class="w-2 h-2 rounded-full bg-zinc-500" id="statusDot"></span>
          <span id="statusText">Idle</span>
        </div>
        <button onclick="refreshAll()" class="p-2 rounded-full bg-zinc-900/80 hover:bg-zinc-800 text-zinc-300 transition" title="Refresh">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
        </button>
      </div>
    </div>
  </header>

  <!-- Mobile Segmented Bar -->
  <div class="sm:hidden px-4 pt-3">
    <div class="flex items-center justify-between bg-zinc-900/90 p-1 rounded-full border border-white/5 text-xs w-full">
      <button onclick="switchView('remote')" id="tabViewRemoteMobile" class="flex-1 py-1.5 rounded-full bg-zinc-800 text-white font-medium text-center transition">Remote</button>
      <button onclick="switchView('media')" id="tabViewMediaMobile" class="flex-1 py-1.5 rounded-full text-zinc-400 hover:text-white text-center transition">Media</button>
      <button onclick="switchView('settings')" id="tabViewSettingsMobile" class="flex-1 py-1.5 rounded-full text-zinc-400 hover:text-white text-center transition">Wi-Fi</button>
    </div>
  </div>

  <main class="max-w-5xl mx-auto px-4 py-6 sm:px-6 flex-1 w-full space-y-6">

    <!-- VIEW: Remote & Now Playing -->
    <div id="viewRemote" class="space-y-6">
      <!-- Apple Glass Remote Card -->
      <section class="apple-glass rounded-3xl p-6 sm:p-8 space-y-6">
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div class="space-y-1.5">
            <span class="text-[11px] font-semibold tracking-widest text-blue-400 uppercase font-mono">Display Remote</span>
            <h2 class="text-xl sm:text-2xl font-semibold text-white tracking-tight truncate max-w-lg" id="nowPlayingText">No active playback</h2>
          </div>
          <div class="flex items-center gap-3 text-xs font-mono text-zinc-400 bg-black/40 px-3.5 py-1.5 rounded-xl border border-white/5 self-start sm:self-center">
            <span id="posDuration">0:00 / 0:00</span>
            <span class="text-zinc-600">&bull;</span>
            <span id="storageInfo">Disk: --</span>
          </div>
        </div>

        <!-- Touch-Friendly Tactile Controls -->
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-6 pt-4 border-t border-white/5">
          <div class="flex items-center gap-3 justify-center sm:justify-start">
            <button onclick="controlAction('prev')" class="apple-pill p-3.5 rounded-2xl text-zinc-200 active:scale-95" title="Previous">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M15 19l-7-7 7-7"/></svg>
            </button>
            <button id="playBtn" onclick="controlAction('start')" class="apple-btn-primary px-7 py-3.5 rounded-2xl text-white font-medium flex items-center gap-2.5 shadow-lg active:scale-95">
              <svg class="w-5 h-5 fill-current" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
              <span>Play</span>
            </button>
            <button id="pauseBtn" onclick="controlAction('pause')" class="apple-pill p-3.5 rounded-2xl text-zinc-200 active:scale-95" title="Pause / Resume">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M10 9v6m4-6v6"/></svg>
            </button>
            <button id="stopBtn" onclick="controlAction('stop')" class="apple-btn-danger p-3.5 rounded-2xl active:scale-95" title="Stop">
              <svg class="w-5 h-5 fill-current" viewBox="0 0 24 24"><path d="M6 6h12v12H6z"/></svg>
            </button>
            <button onclick="controlAction('next')" class="apple-pill p-3.5 rounded-2xl text-zinc-200 active:scale-95" title="Next">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M9 5l7 7-7 7"/></svg>
            </button>
          </div>

          <!-- Quick Sliders (Volume & Slide Duration) -->
          <div class="flex items-center gap-6 justify-center sm:justify-end">
            <!-- Volume Slider -->
            <div class="flex items-center gap-2.5">
              <svg class="w-4 h-4 text-zinc-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.536 8.464a5 5 0 010 7.072m2.828-9.9a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z"/></svg>
              <input type="range" id="volumeSlider" min="0" max="100" value="100" onchange="updateVolume(this.value)" class="w-20 sm:w-24" />
            </div>

            <!-- Slide Timing -->
            <div class="flex items-center gap-2 text-xs text-zinc-400">
              <span>Interval:</span>
              <select id="slideDuration" onchange="updateDuration(this.value)" class="bg-black/60 border border-white/10 rounded-xl px-2.5 py-1.5 text-zinc-200 text-xs focus:outline-none">
                <option value="3">3s</option>
                <option value="5">5s</option>
                <option value="10">10s</option>
                <option value="15">15s</option>
                <option value="30">30s</option>
              </select>
            </div>

            <button onclick="controlAction('fullscreen')" class="apple-pill p-2.5 rounded-xl text-zinc-300" title="Toggle Fullscreen">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4"/></svg>
            </button>
          </div>
        </div>
      </section>

      <!-- Instant Upload Zone -->
      <section>
        <div id="dropZone" class="border border-dashed border-white/10 hover:border-blue-500/50 bg-zinc-950/40 rounded-3xl p-8 text-center transition-all cursor-pointer flex flex-col items-center justify-center gap-3">
          <input type="file" id="fileInput" multiple accept="image/*,video/*" class="hidden" />
          <div class="w-12 h-12 rounded-2xl bg-zinc-900 border border-white/5 flex items-center justify-center text-blue-400 shadow-inner">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/></svg>
          </div>
          <div>
            <p class="text-sm font-medium text-zinc-200">Drag media files here or tap to upload</p>
            <p class="text-xs text-zinc-500 mt-0.5">Supports 4K/1080p MP4, MOV, MKV, JPG, PNG, GIF, WebM</p>
          </div>
          <div id="uploadProgressContainer" class="w-full max-w-sm hidden mt-3">
            <div class="w-full bg-zinc-900 rounded-full h-1.5 overflow-hidden">
              <div id="uploadProgressBar" class="bg-blue-500 h-full transition-all duration-200" style="width: 0%"></div>
            </div>
            <p id="uploadStatusText" class="text-xs text-zinc-400 mt-2 font-mono">Uploading...</p>
          </div>
        </div>
      </section>
    </div>

    <!-- VIEW: Media Library Grid -->
    <div id="viewMedia" class="space-y-6 hidden">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div class="flex items-center gap-2.5">
          <h3 class="text-lg font-semibold text-white tracking-tight">Media Library</h3>
          <span class="text-xs px-2.5 py-0.5 rounded-full bg-zinc-900 text-zinc-400 font-mono border border-white/5" id="fileCountBadge">0</span>
        </div>

        <div class="flex items-center gap-3">
          <div class="relative">
            <input type="text" id="searchInput" placeholder="Search..." oninput="filterMedia()" class="bg-zinc-900/90 border border-white/10 text-zinc-200 text-xs rounded-full px-3.5 py-2 pl-9 focus:outline-none focus:border-blue-500 w-48" />
            <svg class="w-4 h-4 text-zinc-500 absolute left-3 top-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
          </div>
          <div class="flex bg-zinc-900/90 p-1 rounded-full border border-white/5 text-xs">
            <button onclick="setTab('all')" id="tabAll" class="px-3 py-1 rounded-full bg-zinc-800 text-white font-medium transition">All</button>
            <button onclick="setTab('video')" id="tabVideo" class="px-3 py-1 rounded-full text-zinc-400 hover:text-white transition">Videos</button>
            <button onclick="setTab('image')" id="tabImage" class="px-3 py-1 rounded-full text-zinc-400 hover:text-white transition">Images</button>
          </div>
        </div>
      </div>

      <div id="mediaGrid" class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
        <!-- Media Cards -->
      </div>
      <div id="emptyState" class="hidden py-16 text-center text-zinc-500 space-y-2">
        <p class="text-sm">No media files in library</p>
        <p class="text-xs text-zinc-600">Drag media files into the upload zone to begin playback</p>
      </div>
    </div>

    <!-- VIEW: Wi-Fi & Settings -->
    <div id="viewSettings" class="space-y-6 hidden">
      <!-- Wi-Fi Networking Card -->
      <section class="apple-glass rounded-3xl p-6 sm:p-8 space-y-6">
        <div class="flex items-center justify-between">
          <div class="space-y-1">
            <span class="text-[11px] font-semibold tracking-widest text-blue-400 uppercase font-mono">Zero-Config Wi-Fi</span>
            <h3 class="text-lg font-semibold text-white">Wireless Networks</h3>
            <p class="text-xs text-zinc-400">Easily connect to new venue, home, or office Wi-Fi networks on the go.</p>
          </div>
          <button onclick="scanWifi()" id="scanWifiBtn" class="apple-pill px-4 py-2 rounded-xl text-xs font-medium text-white flex items-center gap-1.5">
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
            <span>Scan Wi-Fi</span>
          </button>
        </div>

        <div id="wifiList" class="space-y-2.5">
          <div class="text-center py-6 text-zinc-500 text-xs font-mono">Tap "Scan Wi-Fi" to discover nearby wireless networks</div>
        </div>
      </section>

      <!-- Security & Device Preferences -->
      <section class="apple-glass rounded-3xl p-6 sm:p-8 space-y-6">
        <div class="space-y-1">
          <span class="text-[11px] font-semibold tracking-widest text-emerald-400 uppercase font-mono">Security &amp; Storage</span>
          <h3 class="text-lg font-semibold text-white">Appliance Protection</h3>
          <p class="text-xs text-zinc-400">Set an optional 4-digit PIN for public exhibitions, or leave blank for seamless local access.</p>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-2">
          <div class="apple-card rounded-2xl p-5 space-y-3">
            <label class="text-xs font-medium text-zinc-300">Access PIN (Optional)</label>
            <div class="flex items-center gap-2">
              <input type="password" id="pinInput" placeholder="Leave empty for open access" class="bg-black/60 border border-white/10 text-xs text-zinc-200 rounded-xl px-3.5 py-2.5 w-full focus:outline-none focus:border-blue-500" />
              <button onclick="savePin()" class="apple-btn-primary px-4 py-2.5 rounded-xl text-xs font-medium text-white">Save</button>
            </div>
            <p class="text-[11px] text-zinc-500">When set, visitors must enter this PIN to control playback or delete media.</p>
          </div>

          <div class="apple-card rounded-2xl p-5 space-y-2 text-xs">
            <span class="text-zinc-400 font-medium">Appliance Status</span>
            <div class="space-y-1 text-zinc-300 font-mono text-[11px]">
              <div>Operating Mode: Standalone IPC</div>
              <div id="cpuTempText">CPU Temp: --</div>
              <div id="diskFreeText">Free Storage: --</div>
            </div>
          </div>
        </div>
      </section>
    </div>

  </main>

  <footer class="border-t border-white/5 py-4 px-6 text-center text-[11px] text-zinc-600 font-mono">
    PiMedia Appliance &bull; Apple Design Philosophy &bull; Linux &amp; Raspberry Pi
  </footer>

  <!-- Connect Wi-Fi Modal -->
  <div id="wifiModal" class="fixed inset-0 z-50 bg-black/80 backdrop-blur-md flex items-center justify-center p-4 hidden">
    <div class="apple-glass rounded-3xl p-6 sm:p-7 max-w-sm w-full space-y-4">
      <div class="space-y-1">
        <h4 class="text-base font-semibold text-white" id="modalSsidTitle">Connect to Wi-Fi</h4>
        <p class="text-xs text-zinc-400">Enter network passphrase to connect.</p>
      </div>
      <input type="password" id="modalWifiPassword" placeholder="Passphrase" class="bg-black/60 border border-white/10 text-xs text-zinc-200 rounded-xl px-3.5 py-3 w-full focus:outline-none focus:border-blue-500" />
      <div class="flex items-center justify-end gap-2.5 pt-2">
        <button onclick="closeWifiModal()" class="apple-pill px-4 py-2 rounded-xl text-xs text-zinc-300">Cancel</button>
        <button onclick="submitWifiConnect()" class="apple-btn-primary px-5 py-2 rounded-xl text-xs font-medium text-white">Connect</button>
      </div>
    </div>
  </div>

  <script>
    let mediaItems = [];
    let activeTab = 'all';
    let targetSsid = '';

    function switchView(viewName) {
      const views = ['Remote', 'Media', 'Settings'];
      views.forEach(v => {
        const el = document.getElementById('view' + v);
        const tab = document.getElementById('tabView' + v);
        const tabMobile = document.getElementById('tabView' + v + 'Mobile');
        
        if (v.toLowerCase() === viewName) {
          if (el) el.classList.remove('hidden');
          if (tab) tab.className = 'px-4 py-1.5 rounded-full bg-zinc-800 text-white font-medium transition shadow-sm';
          if (tabMobile) tabMobile.className = 'flex-1 py-1.5 rounded-full bg-zinc-800 text-white font-medium text-center transition';
        } else {
          if (el) el.classList.add('hidden');
          if (tab) tab.className = 'px-4 py-1.5 rounded-full text-zinc-400 hover:text-white transition';
          if (tabMobile) tabMobile.className = 'flex-1 py-1.5 rounded-full text-zinc-400 hover:text-white text-center transition';
        }
      });
    }

    async function fetchStatus() {
      try {
        const res = await fetch('/api/status');
        if (!res.ok) return;
        const data = await res.json();
        
        const hostEl = document.getElementById('hostIp');
        if (hostEl) hostEl.textContent = `${data.system.ip}:5000`;
        
        const storageEl = document.getElementById('storageInfo');
        if (storageEl) storageEl.textContent = `Free: ${data.system.disk_free_gb} GB (${data.system.disk_used_percent}%)`;

        const cpuTempEl = document.getElementById('cpuTempText');
        if (cpuTempEl && data.system.cpu_temp) cpuTempEl.textContent = `CPU Temp: ${data.system.cpu_temp}°C`;

        const diskFreeText = document.getElementById('diskFreeText');
        if (diskFreeText) diskFreeText.textContent = `Free Storage: ${data.system.disk_free_gb} GB / ${data.system.disk_total_gb} GB`;

        const badge = document.getElementById('statusBadge');
        const dot = document.getElementById('statusDot');
        const text = document.getElementById('statusText');
        const nowPlaying = document.getElementById('nowPlayingText');
        const posDuration = document.getElementById('posDuration');

        if (data.playback.is_running) {
          badge.className = 'flex items-center gap-2 px-3 py-1 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20';
          dot.className = 'w-2 h-2 rounded-full bg-emerald-400 animate-pulse';
          text.textContent = data.playback.paused ? 'Paused' : 'Playing';
          nowPlaying.textContent = data.playback.filename || 'Active Playlist';
          posDuration.textContent = formatTime(data.playback.position) + ' / ' + formatTime(data.playback.duration);
        } else {
          badge.className = 'flex items-center gap-2 px-3 py-1 rounded-full text-xs font-medium bg-zinc-900 text-zinc-400 border border-white/5';
          dot.className = 'w-2 h-2 rounded-full bg-zinc-500';
          text.textContent = 'Idle';
          nowPlaying.textContent = 'No active playback';
          posDuration.textContent = '0:00 / 0:00';
        }

        const durSelect = document.getElementById('slideDuration');
        if (durSelect && data.config.image_duration) {
          durSelect.value = String(data.config.image_duration);
        }
      } catch (err) {
        console.error(err);
      }
    }

    async function fetchFiles() {
      try {
        const res = await fetch('/api/files');
        if (!res.ok) return;
        mediaItems = await res.json();
        document.getElementById('fileCountBadge').textContent = mediaItems.length;
        renderGrid();
      } catch (err) {
        console.error(err);
      }
    }

    function formatBytes(bytes) {
      if (bytes === 0) return '0 B';
      const k = 1024;
      const sizes = ['B', 'KB', 'MB', 'GB'];
      const i = Math.floor(Math.log(bytes) / Math.log(k));
      return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    }

    function formatTime(sec) {
      if (!sec || isNaN(sec)) return '0:00';
      const m = Math.floor(sec / 60);
      const s = Math.floor(sec % 60);
      return `${m}:${s < 10 ? '0' : ''}${s}`;
    }

    function renderGrid() {
      const grid = document.getElementById('mediaGrid');
      const empty = document.getElementById('emptyState');
      const query = document.getElementById('searchInput').value.toLowerCase().trim();

      const filtered = mediaItems.filter(item => {
        if (activeTab !== 'all' && item.type !== activeTab) return false;
        if (query && !item.name.toLowerCase().includes(query)) return false;
        return true;
      });

      if (filtered.length === 0) {
        grid.innerHTML = '';
        empty.classList.remove('hidden');
        return;
      }
      empty.classList.add('hidden');

      grid.innerHTML = filtered.map(item => `
        <div class="group relative apple-card rounded-2xl overflow-hidden flex flex-col">
          <div class="relative w-full aspect-video bg-black flex items-center justify-center overflow-hidden">
            ${item.type === 'video' 
              ? `<video src="/media/${encodeURIComponent(item.name)}" class="w-full h-full object-cover" preload="metadata"></video>
                 <div class="absolute inset-0 bg-black/40 flex items-center justify-center pointer-events-none group-hover:bg-black/10 transition">
                   <div class="p-2.5 rounded-full bg-black/70 text-white backdrop-blur-md">
                     <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
                   </div>
                 </div>`
              : `<img src="/media/${encodeURIComponent(item.name)}" class="w-full h-full object-cover loading="lazy" />`
            }
            <button onclick="playDirect('${item.name}')" class="absolute inset-0 z-10 opacity-0 group-hover:opacity-100 bg-blue-600/30 backdrop-blur-sm flex items-center justify-center transition text-xs font-semibold text-white gap-1.5">
              <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
              <span>Play Now</span>
            </button>
            <button onclick="deleteFile('${item.name}')" class="absolute top-2 right-2 z-20 p-1.5 rounded-full bg-black/60 hover:bg-rose-600 text-zinc-400 hover:text-white transition backdrop-blur opacity-0 group-hover:opacity-100" title="Delete">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
            </button>
          </div>
          <div class="p-3 flex flex-col justify-between flex-1 gap-1">
            <p class="text-xs font-medium text-zinc-200 truncate" title="${item.name}">${item.name}</p>
            <div class="flex items-center justify-between text-[10px] text-zinc-500 font-mono">
              <span>${formatBytes(item.size)}</span>
              <span class="uppercase tracking-wider font-semibold text-zinc-400">${item.extension}</span>
            </div>
          </div>
        </div>
      `).join('');
    }

    function setTab(tab) {
      activeTab = tab;
      ['All', 'Video', 'Image'].forEach(t => {
        const el = document.getElementById('tab' + t);
        if (t.toLowerCase() === tab) {
          el.className = 'px-3 py-1 rounded-full bg-zinc-800 text-white font-medium transition';
        } else {
          el.className = 'px-3 py-1 rounded-full text-zinc-400 hover:text-white transition';
        }
      });
      renderGrid();
    }

    function filterMedia() {
      renderGrid();
    }

    async function controlAction(action) {
      let endpoint = '/api/playback/' + action;
      if (action === 'start') endpoint = '/api/playback/start';
      try {
        await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        fetchStatus();
      } catch (err) {
        console.error(err);
      }
    }

    async function playDirect(filename) {
      try {
        const dur = document.getElementById('slideDuration').value;
        await fetch('/api/playback/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file: filename, duration: parseInt(dur) })
        });
        fetchStatus();
      } catch (err) {
        console.error(err);
      }
    }

    async function updateVolume(val) {
      try {
        await fetch('/api/playback/volume', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ volume: parseInt(val) })
        });
      } catch (err) {
        console.error(err);
      }
    }

    async function updateDuration(val) {
      try {
        await fetch('/api/playback/duration', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ duration: parseInt(val) })
        });
      } catch (err) {
        console.error(err);
      }
    }

    async function deleteFile(filename) {
      if (!confirm(`Delete ${filename}?`)) return;
      try {
        const res = await fetch('/api/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ filename })
        });
        if (res.ok) fetchFiles();
      } catch (err) {
        console.error(err);
      }
    }

    // Dropzone Upload Handler
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');

    dropZone.onclick = (e) => {
      if (e.target.tagName !== 'INPUT') fileInput.click();
    };

    dropZone.ondragover = (e) => {
      e.preventDefault();
      dropZone.classList.add('drop-active');
    };

    dropZone.ondragleave = () => {
      dropZone.classList.remove('drop-active');
    };

    dropZone.ondrop = (e) => {
      e.preventDefault();
      dropZone.classList.remove('drop-active');
      if (e.dataTransfer.files.length) handleUploads(e.dataTransfer.files);
    };

    fileInput.onchange = (e) => {
      if (e.target.files.length) handleUploads(e.target.files);
    };

    async function handleUploads(files) {
      const container = document.getElementById('uploadProgressContainer');
      const bar = document.getElementById('uploadProgressBar');
      const statusText = document.getElementById('uploadStatusText');

      container.classList.remove('hidden');
      const total = files.length;
      let uploaded = 0;

      for (let i = 0; i < total; i++) {
        const file = files[i];
        statusText.textContent = `Uploading (${i + 1}/${total}): ${file.name}...`;
        const formData = new FormData();
        formData.append('file', file);

        try {
          const res = await fetch('/api/upload', { method: 'POST', body: formData });
          if (res.ok) uploaded++;
        } catch (err) {
          console.error(err);
        }
        bar.style.width = `${((i + 1) / total) * 100}%`;
      }

      statusText.textContent = `Uploaded ${uploaded} of ${total} files.`;
      setTimeout(() => {
        container.classList.add('hidden');
        bar.style.width = '0%';
        fetchFiles();
      }, 1000);
    }

    // Wi-Fi Scanning & Connection
    async function scanWifi() {
      const list = document.getElementById('wifiList');
      const btn = document.getElementById('scanWifiBtn');
      list.innerHTML = '<div class="text-center py-6 text-zinc-400 text-xs font-mono animate-pulse">Scanning nearby networks...</div>';
      btn.disabled = true;

      try {
        const res = await fetch('/api/wifi/scan');
        const data = await res.json();
        btn.disabled = false;

        if (!data || data.length === 0) {
          list.innerHTML = '<div class="text-center py-6 text-zinc-500 text-xs font-mono">No Wi-Fi networks found</div>';
          return;
        }

        list.innerHTML = data.map(net => `
          <div class="apple-card rounded-2xl p-3.5 flex items-center justify-between">
            <div class="flex items-center gap-3">
              <div class="w-8 h-8 rounded-xl ${net.connected ? 'bg-emerald-500/20 text-emerald-400' : 'bg-zinc-800 text-zinc-300'} flex items-center justify-center">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.111 16.404a5.5 5.5 0 017.778 0M12 20h.01m-7.08-7.071c3.904-3.905 10.236-3.905 14.141 0M1.394 9.393c5.857-5.857 15.355-5.857 21.213 0"/></svg>
              </div>
              <div>
                <p class="text-xs font-semibold text-white">${net.ssid}</p>
                <p class="text-[10px] text-zinc-400 font-mono">${net.security} &bull; ${net.signal}% signal ${net.connected ? '&bull; Connected' : ''}</p>
              </div>
            </div>
            ${net.connected 
              ? `<span class="px-3 py-1 rounded-full bg-emerald-500/10 text-emerald-400 text-[11px] font-medium border border-emerald-500/20">Active</span>`
              : `<button onclick="openWifiModal('${net.ssid}')" class="apple-btn-primary px-3.5 py-1.5 rounded-xl text-xs font-medium text-white">Connect</button>`
            }
          </div>
        `).join('');
      } catch (err) {
        btn.disabled = false;
        list.innerHTML = '<div class="text-center py-6 text-rose-400 text-xs font-mono">Failed to scan networks</div>';
      }
    }

    function openWifiModal(ssid) {
      targetSsid = ssid;
      document.getElementById('modalSsidTitle').textContent = `Connect to "${ssid}"`;
      document.getElementById('modalWifiPassword').value = '';
      document.getElementById('wifiModal').classList.remove('hidden');
    }

    function closeWifiModal() {
      document.getElementById('wifiModal').classList.add('hidden');
    }

    async function submitWifiConnect() {
      const pwd = document.getElementById('modalWifiPassword').value;
      closeWifiModal();
      alert(`Connecting to ${targetSsid}...`);
      try {
        const res = await fetch('/api/wifi/connect', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ssid: targetSsid, password: pwd })
        });
        const d = await res.json();
        alert(d.message || d.error);
        scanWifi();
      } catch (err) {
        alert('Network connection request failed');
      }
    }

    async function savePin() {
      const pin = document.getElementById('pinInput').value.trim();
      try {
        const res = await fetch('/api/settings/pin', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pin })
        });
        const d = await res.json();
        alert(d.message);
      } catch (err) {
        alert('Failed to update PIN');
      }
    }

    function refreshAll() {
      fetchStatus();
      fetchFiles();
    }

    // Heartbeat
    refreshAll();
    setInterval(fetchStatus, 3000);
  </script>
</body>
</html>
"""

@app.route("/")
def home():
    return render_template_string(INDEX_HTML)

if __name__ == "__main__":
    for stale in [SOCKET_PATH, PID_PATH, PLAYLIST_PATH]:
        try:
            if os.path.exists(stale):
                os.remove(stale)
        except OSError:
            pass
    print(f"Starting PiMedia Appliance on http://0.0.0.0:5000 (Media: {MEDIA_DIR})")
    app.run(host="0.0.0.0", port=5000, debug=False)
