#!/usr/bin/env python3
"""
PiMedia - Local Media Player and Appliance for Raspberry Pi & Linux Displays
Controls MPV via IPC socket with a modern, responsive web dashboard and REST API.
"""

import os
import sys
import json
import time
import socket
import shutil
import subprocess
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, render_template_string
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Base configuration
MEDIA_DIR = os.environ.get("PIMEDIA_DIR", "/home/pi/media")
if not os.path.exists(MEDIA_DIR) and not os.access("/home/pi", os.W_OK):
    MEDIA_DIR = os.path.expanduser("~/media")

SOCKET_PATH = "/tmp/mpv-socket"
PID_PATH = "/tmp/mpv.pid"
PLAYLIST_PATH = "/tmp/pimedia-playlist.m3u"
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "mp4", "mov", "avi", "mkv", "webm"}
MAX_CONTENT_LENGTH = 1024 * 1024 * 1024  # 1 GB upload cap
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

AUTH_TOKEN = os.environ.get("MEDIA_AUTH_TOKEN")


def require_auth():
    """Verify auth token if configured in environment."""
    if not AUTH_TOKEN:
        return None
    token = request.headers.get("X-Auth-Token") or request.args.get("token")
    if token != AUTH_TOKEN:
        return jsonify({"error": "Unauthorized"}), 401
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
    """Send JSON IPC command to running MPV instance."""
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
    """Verify MPV process status via PID and socket responsiveness."""
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
    """Cleanly shut down MPV and prune runtime socket files."""
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


def start_mpv_playback(target_file=None, image_duration=10):
    """Start MPV in fullscreen loop mode with IPC control."""
    terminate_mpv()
    folder = get_media_dir()
    files = list_media_files()
    if not files:
        return False, "No media files found in library"

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
    """Retrieve host vitals (disk, memory, cpu temp, network IP)."""
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
    }


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
    duration = data.get("duration", 10)

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
    res = send_mpv_command({"command": ["set_property", "image-display-duration", duration]})
    if res is None:
        return jsonify({"error": "MPV not connected"}), 503
    return jsonify({"message": f"Slideshow duration set to {duration}s"})


@app.route("/api/playback/fullscreen", methods=["POST"])
def api_playback_fullscreen():
    auth_res = require_auth()
    if auth_res:
        return auth_res
    res = send_mpv_command({"command": ["cycle", "fullscreen"]})
    if res is None:
        return jsonify({"error": "MPV not connected"}), 503
    return jsonify({"message": "Toggled fullscreen"})


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
# Modern Web Dashboard
# ==========================================

INDEX_HTML = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
  <title>PiMedia Control Center</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            brand: {
              50: '#f4f4f5',
              100: '#e4e4e7',
              500: '#3b82f6',
              600: '#2563eb',
              700: '#1d4ed8',
            }
          }
        }
      }
    }
  </script>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
    body { font-family: 'Inter', sans-serif; }
    code, .font-mono { font-family: 'JetBrains Mono', monospace; }
    .drop-active { border-color: #3b82f6 !important; background-color: rgba(59, 130, 246, 0.08) !important; }
  </style>
</head>
<body class="bg-zinc-950 text-zinc-100 min-h-screen flex flex-col antialiased selection:bg-blue-600 selection:text-white">

  <!-- Header -->
  <header class="border-b border-zinc-800/80 bg-zinc-900/60 backdrop-blur sticky top-0 z-30 px-4 py-3 sm:px-6">
    <div class="max-w-6xl mx-auto flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center text-white font-bold text-sm tracking-wider shadow-md shadow-blue-500/20">
          PI
        </div>
        <div>
          <h1 class="text-base font-semibold tracking-tight text-white leading-tight">PiMedia</h1>
          <p class="text-xs text-zinc-400 font-mono" id="hostIp">Connecting...</p>
        </div>
      </div>
      
      <div class="flex items-center gap-2">
        <div id="statusBadge" class="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-zinc-800 text-zinc-400 border border-zinc-700/60">
          <span class="w-2 h-2 rounded-full bg-zinc-500" id="statusDot"></span>
          <span id="statusText">Checking</span>
        </div>
        <button onclick="refreshAll()" class="p-2 rounded-lg bg-zinc-800/80 hover:bg-zinc-700 text-zinc-300 transition-colors" title="Refresh">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
        </button>
      </div>
    </div>
  </header>

  <main class="max-w-6xl mx-auto px-4 py-6 sm:px-6 flex-1 w-full space-y-6">

    <!-- Active Remote Playback Bar -->
    <section class="bg-zinc-900/90 border border-zinc-800 rounded-2xl p-5 shadow-xl shadow-black/40 space-y-4">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div class="space-y-1">
          <span class="text-xs font-semibold uppercase tracking-wider text-blue-400 font-mono">Display Remote</span>
          <h2 class="text-lg font-semibold text-white truncate max-w-md" id="nowPlayingText">No active playback</h2>
        </div>
        <div class="flex items-center gap-4 text-xs font-mono text-zinc-400">
          <span id="posDuration">0:00 / 0:00</span>
          <span id="storageInfo">Disk: --</span>
        </div>
      </div>

      <!-- Controls Row -->
      <div class="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-zinc-800/80">
        <div class="flex items-center gap-2">
          <button onclick="controlAction('prev')" class="p-3 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-200 transition active:scale-95">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/></svg>
          </button>
          <button id="playBtn" onclick="controlAction('start')" class="px-5 py-3 rounded-xl bg-blue-600 hover:bg-blue-500 text-white font-medium flex items-center gap-2 transition shadow-lg shadow-blue-600/25 active:scale-95">
            <svg class="w-5 h-5 fill-current" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
            <span>Play</span>
          </button>
          <button id="pauseBtn" onclick="controlAction('pause')" class="p-3 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-200 transition active:scale-95">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 9v6m4-6v6"/></svg>
          </button>
          <button id="stopBtn" onclick="controlAction('stop')" class="p-3 rounded-xl bg-rose-600/20 hover:bg-rose-600/30 text-rose-300 border border-rose-500/30 transition active:scale-95">
            <svg class="w-5 h-5 fill-current" viewBox="0 0 24 24"><path d="M6 6h12v12H6z"/></svg>
          </button>
          <button onclick="controlAction('next')" class="p-3 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-200 transition active:scale-95">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>
          </button>
        </div>

        <div class="flex items-center gap-4 flex-1 max-w-xs justify-end">
          <div class="flex items-center gap-2 text-xs text-zinc-400">
            <span>Slide:</span>
            <select id="slideDuration" onchange="updateDuration(this.value)" class="bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1 text-zinc-200 text-xs focus:outline-none">
              <option value="3">3s</option>
              <option value="5">5s</option>
              <option value="10" selected>10s</option>
              <option value="15">15s</option>
              <option value="30">30s</option>
            </select>
          </div>
          <button onclick="controlAction('fullscreen')" class="p-2.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition" title="Toggle Fullscreen">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4"/></svg>
          </button>
        </div>
      </div>
    </section>

    <!-- Upload Dropzone -->
    <section>
      <div id="dropZone" class="border-2 border-dashed border-zinc-800 hover:border-zinc-700 bg-zinc-900/40 rounded-2xl p-6 text-center transition-all cursor-pointer flex flex-col items-center justify-center gap-2">
        <input type="file" id="fileInput" multiple accept="image/*,video/*" class="hidden" />
        <div class="w-10 h-10 rounded-full bg-zinc-800 flex items-center justify-center text-zinc-300">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/></svg>
        </div>
        <div>
          <p class="text-sm font-medium text-zinc-200">Drag photos or videos here, or tap to browse</p>
          <p class="text-xs text-zinc-500 mt-0.5">Supports MP4, MOV, MKV, JPG, PNG, GIF, WebM up to 1GB</p>
        </div>
        <div id="uploadProgressContainer" class="w-full max-w-md hidden mt-2">
          <div class="w-full bg-zinc-800 rounded-full h-2 overflow-hidden">
            <div id="uploadProgressBar" class="bg-blue-600 h-full transition-all duration-200" style="width: 0%"></div>
          </div>
          <p id="uploadStatusText" class="text-xs text-zinc-400 mt-1.5 font-mono">Uploading...</p>
        </div>
      </div>
    </section>

    <!-- Gallery Header & Filter -->
    <section class="space-y-4">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div class="flex items-center gap-2">
          <h3 class="text-base font-semibold text-white">Media Library</h3>
          <span class="text-xs px-2 py-0.5 rounded-full bg-zinc-800 text-zinc-400 font-mono" id="fileCountBadge">0</span>
        </div>

        <div class="flex items-center gap-2">
          <div class="relative">
            <input type="text" id="searchInput" placeholder="Search files..." oninput="filterMedia()" class="bg-zinc-900 border border-zinc-800 text-zinc-200 text-xs rounded-xl px-3 py-2 pl-8 focus:outline-none focus:border-blue-500 w-44" />
            <svg class="w-3.5 h-3.5 text-zinc-500 absolute left-2.5 top-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
          </div>
          <div class="flex bg-zinc-900 p-1 rounded-xl border border-zinc-800 text-xs">
            <button onclick="setTab('all')" id="tabAll" class="px-2.5 py-1 rounded-lg bg-zinc-800 text-white font-medium transition">All</button>
            <button onclick="setTab('video')" id="tabVideo" class="px-2.5 py-1 rounded-lg text-zinc-400 hover:text-zinc-200 transition">Videos</button>
            <button onclick="setTab('image')" id="tabImage" class="px-2.5 py-1 rounded-lg text-zinc-400 hover:text-zinc-200 transition">Images</button>
          </div>
        </div>
      </div>

      <!-- Media Grid -->
      <div id="mediaGrid" class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3.5">
        <!-- Cards injected dynamically -->
      </div>
      <div id="emptyState" class="hidden py-16 text-center text-zinc-500 space-y-2">
        <p class="text-sm">No media files found</p>
        <p class="text-xs text-zinc-600">Upload media using the dropzone above to begin playback</p>
      </div>
    </section>

  </main>

  <footer class="border-t border-zinc-900 py-4 px-6 text-center text-xs text-zinc-600 font-mono">
    PiMedia Appliance &bull; Linux &amp; Raspberry Pi
  </footer>

  <script>
    let mediaItems = [];
    let activeTab = 'all';

    async function fetchStatus() {
      try {
        const res = await fetch('/api/status');
        if (!res.ok) return;
        const data = await res.json();
        
        const hostEl = document.getElementById('hostIp');
        if (hostEl) hostEl.textContent = `${data.system.ip} : 5000`;
        
        const storageEl = document.getElementById('storageInfo');
        if (storageEl) storageEl.textContent = `Free: ${data.system.disk_free_gb} GB (${data.system.disk_used_percent}% used)`;

        const badge = document.getElementById('statusBadge');
        const dot = document.getElementById('statusDot');
        const text = document.getElementById('statusText');
        const nowPlaying = document.getElementById('nowPlayingText');
        const posDuration = document.getElementById('posDuration');

        if (data.playback.is_running) {
          badge.className = 'flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20';
          dot.className = 'w-2 h-2 rounded-full bg-emerald-400 animate-pulse';
          text.textContent = data.playback.paused ? 'Paused' : 'Playing';
          nowPlaying.textContent = data.playback.filename || 'Active Playlist';
          posDuration.textContent = formatTime(data.playback.position) + ' / ' + formatTime(data.playback.duration);
        } else {
          badge.className = 'flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-zinc-800 text-zinc-400 border border-zinc-700/60';
          dot.className = 'w-2 h-2 rounded-full bg-zinc-500';
          text.textContent = 'Idle';
          nowPlaying.textContent = 'No active playback';
          posDuration.textContent = '0:00 / 0:00';
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
        <div class="group relative bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden hover:border-zinc-700 transition flex flex-col">
          <div class="relative w-full aspect-video bg-zinc-950 flex items-center justify-center overflow-hidden">
            ${item.type === 'video' 
              ? `<video src="/media/${encodeURIComponent(item.name)}" class="w-full h-full object-cover" preload="metadata"></video>
                 <div class="absolute inset-0 bg-black/30 flex items-center justify-center pointer-events-none group-hover:bg-black/10 transition">
                   <div class="p-2 rounded-full bg-black/60 text-white backdrop-blur">
                     <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
                   </div>
                 </div>`
              : `<img src="/media/${encodeURIComponent(item.name)}" class="w-full h-full object-cover" loading="lazy" />`
            }
            <button onclick="playDirect('${item.name}')" class="absolute inset-0 z-10 opacity-0 group-hover:opacity-100 bg-blue-600/20 backdrop-blur-sm flex items-center justify-center transition text-xs font-semibold text-white gap-1.5">
              <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
              <span>Play Now</span>
            </button>
            <button onclick="deleteFile('${item.name}')" class="absolute top-2 right-2 z-20 p-1.5 rounded-lg bg-zinc-900/80 hover:bg-rose-600 text-zinc-400 hover:text-white transition backdrop-blur opacity-0 group-hover:opacity-100" title="Delete">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
            </button>
          </div>
          <div class="p-2.5 flex flex-col justify-between flex-1 gap-1">
            <p class="text-xs font-medium text-zinc-200 truncate" title="${item.name}">${item.name}</p>
            <div class="flex items-center justify-between text-[10px] text-zinc-500 font-mono">
              <span>${formatBytes(item.size)}</span>
              <span class="uppercase">${item.extension}</span>
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
          el.className = 'px-2.5 py-1 rounded-lg bg-zinc-800 text-white font-medium transition';
        } else {
          el.className = 'px-2.5 py-1 rounded-lg text-zinc-400 hover:text-zinc-200 transition';
        }
      });
      renderGrid();
    }

    function filterMedia() {
      renderGrid();
    }

    async function controlAction(action) {
      let endpoint = '/api/playback/' + action;
      if (action === 'start') {
        endpoint = '/api/playback/start';
      }
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
        if (res.ok) {
          fetchFiles();
        }
      } catch (err) {
        console.error(err);
      }
    }

    // Dropzone & Upload Handler
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
      }, 1200);
    }

    function refreshAll() {
      fetchStatus();
      fetchFiles();
    }

    // Initialization & Heartbeat
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
    print("Starting PiMedia Server on http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
