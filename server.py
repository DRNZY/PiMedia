#!/usr/bin/env python3
"""
PiMedia local media player and display controller.
Controls mpv via UNIX socket with a web remote and REST API.
"""

import os
import sys
import json
import time
import socket
import shutil
import subprocess
import threading
from datetime import datetime
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
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "svg", "mp4", "mov", "avi", "mkv", "webm", "mp3", "flac", "wav", "ogg", "m4a", "aac"}
MAX_CONTENT_LENGTH = 1024 * 1024 * 1024  # 1 GB cap
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

def load_config():
    defaults = {
        "auth_pin": "",
        "image_duration": 10,
        "auto_loop": True,
        "ken_burns": False,
        "background_audio": "",
        "cec_enabled": False,
        "display_schedule": {
            "enabled": False,
            "sleep_time": "23:00",
            "wake_time": "07:00"
        }
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
                defaults.update(saved)
                return defaults
        except Exception:
            pass
    return defaults

def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as err:
        print(f"Failed to save config: {err}", file=sys.stderr)

CONFIG = load_config()

def require_auth():
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
    if ext in {"mp3", "flac", "wav", "ogg", "m4a", "aac"}:
        return "audio"
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

def start_mpv_playback(target_file=None, image_duration=None, is_stream_url=False):
    terminate_mpv()
    folder = get_media_dir()
    
    if is_stream_url and target_file:
        # Stream URL directly (YouTube, RTSP, HLS, Web Video)
        cmd = [
            "mpv",
            "--fs",
            f"--input-ipc-server={SOCKET_PATH}",
            "--no-osc",
            "--no-osd-bar",
            "--quiet",
            "--keep-open=yes",
            target_file
        ]
    else:
        files = [f for f in list_media_files() if f["type"] in {"image", "video"}]
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

        # Ken Burns Motion transitions for photos
        if CONFIG.get("ken_burns"):
            cmd.extend(["--video-pan-x=0.02", "--video-pan-y=0.02", "--video-zoom=0.08"])

        # Background Audio integration
        bg_audio = CONFIG.get("background_audio")
        if bg_audio:
            bg_path = os.path.join(folder, bg_audio) if not bg_audio.startswith("http") else bg_audio
            if os.path.exists(bg_path) or bg_audio.startswith("http"):
                cmd.append(f"--audio-file={bg_path}")

    env = os.environ.copy()
    if "DISPLAY" not in env:
        env["DISPLAY"] = ":0"

    try:
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(PID_PATH, "w", encoding="utf-8") as f:
            f.write(str(proc.pid))
        time.sleep(0.5)
        return True, "Playback started"
    except Exception as err:
        return False, f"Failed to spawn mpv: {err}"

# Display power and scheduling

def set_display_power(state: bool):
    """Toggle HDMI / Display power state."""
    # Raspberry Pi vcgencmd
    if shutil.which("vcgencmd"):
        try:
            val = "1" if state else "0"
            subprocess.run(["vcgencmd", "display_power", val], check=False)
        except Exception:
            pass

    # Wayland wlr-randr
    if shutil.which("wlr-randr"):
        try:
            action = "--on" if state else "--off"
            subprocess.run(["wlr-randr", "--output", "HDMI-A-1", action], check=False)
        except Exception:
            pass

    # X11 DPMS
    if shutil.which("xset"):
        try:
            env = os.environ.copy()
            if "DISPLAY" not in env:
                env["DISPLAY"] = ":0"
            action = "on" if state else "off"
            subprocess.run(["xset", "dpms", "force", action], env=env, check=False)
        except Exception:
            pass

    # Pause or resume MPV
    if is_mpv_alive():
        send_mpv_command({"command": ["set_property", "pause", not state]})

    return True

def display_scheduler_daemon():
    """Background daemon checking display sleep/wake schedule."""
    while True:
        try:
            sched = CONFIG.get("display_schedule", {})
            if sched.get("enabled"):
                now_str = datetime.now().strftime("%H:%M")
                sleep_t = sched.get("sleep_time", "23:00")
                wake_t = sched.get("wake_time", "07:00")

                if now_str == sleep_t:
                    set_display_power(False)
                elif now_str == wake_t:
                    set_display_power(True)
        except Exception:
            pass
        time.sleep(30)

threading.Thread(target=display_scheduler_daemon, daemon=True).start()

# HDMI-CEC TV remote listener

def cec_listener_daemon():
    """Listens for TV remote button presses via cec-client and controls MPV."""
    if not shutil.which("cec-client") or not CONFIG.get("cec_enabled"):
        return

    try:
        proc = subprocess.Popen(
            ["cec-client", "-d", "1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True
        )
        for line in proc.stdout:
            if not CONFIG.get("cec_enabled"):
                break
            if "key pressed:" in line:
                key = line.split("key pressed:")[1].strip().lower()
                if "play" in key or "select" in key:
                    send_mpv_command({"command": ["cycle", "pause"]})
                elif "pause" in key:
                    send_mpv_command({"command": ["set_property", "pause", True]})
                elif "stop" in key:
                    terminate_mpv()
                elif "forward" in key or "right" in key:
                    send_mpv_command({"command": ["playlist-next"]})
                elif "backward" in key or "left" in key:
                    send_mpv_command({"command": ["playlist-prev"]})
                elif "up" in key:
                    send_mpv_command({"command": ["add", "volume", 5]})
                elif "down" in key:
                    send_mpv_command({"command": ["add", "volume", -5]})
    except Exception:
        pass

# System vitals and Wi-Fi

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
        "yt_dlp_available": shutil.which("yt-dlp") is not None,
        "cec_available": shutil.which("cec-client") is not None,
    }

def scan_wifi_networks():
    networks = []
    seen = set()
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
    return False, "nmcli required for Wi-Fi configuration"

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
        "config": CONFIG
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

@app.route("/api/playback/stream", methods=["POST"])
def api_playback_stream():
    """Stream a web video / YouTube link directly to MPV."""
    auth_res = require_auth()
    if auth_res:
        return auth_res

    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "Stream URL is required"}), 400

    ok, msg = start_mpv_playback(target_file=url, is_stream_url=True)
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"message": f"Streaming {url}"})

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

@app.route("/api/playback/seek", methods=["POST"])
def api_playback_seek():
    auth_res = require_auth()
    if auth_res:
        return auth_res
    data = request.get_json(silent=True) or {}
    if "seconds" in data:
        secs = float(data["seconds"])
        res = send_mpv_command({"command": ["seek", secs, "relative"]})
    elif "position" in data:
        pos = float(data["position"])
        res = send_mpv_command({"command": ["seek", pos, "absolute"]})
    else:
        return jsonify({"error": "Missing seek parameters"}), 400
    if res is None:
        return jsonify({"error": "MPV not connected"}), 503
    return jsonify({"message": "Seek complete"})

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

@app.route("/api/playback/kenburns", methods=["POST"])
def api_playback_kenburns():
    auth_res = require_auth()
    if auth_res:
        return auth_res
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", False))
    CONFIG["ken_burns"] = enabled
    save_config(CONFIG)
    return jsonify({"message": f"Ken Burns motion {'enabled' if enabled else 'disabled'}", "ken_burns": enabled})

@app.route("/api/playback/background_audio", methods=["POST"])
def api_playback_background_audio():
    auth_res = require_auth()
    if auth_res:
        return auth_res
    data = request.get_json(silent=True) or {}
    audio_file = data.get("audio_file", "").strip()
    CONFIG["background_audio"] = audio_file
    save_config(CONFIG)
    return jsonify({"message": "Background audio updated", "background_audio": audio_file})

@app.route("/api/playback/fullscreen", methods=["POST"])
def api_playback_fullscreen():
    auth_res = require_auth()
    if auth_res:
        return auth_res
    res = send_mpv_command({"command": ["cycle", "fullscreen"]})
    if res is None:
        return jsonify({"error": "MPV not connected"}), 503
    return jsonify({"message": "Toggled fullscreen"})

@app.route("/api/display/power", methods=["POST"])
def api_display_power():
    auth_res = require_auth()
    if auth_res:
        return auth_res
    data = request.get_json(silent=True) or {}
    state = bool(data.get("power", True))
    set_display_power(state)
    return jsonify({"message": f"Display power set to {'ON' if state else 'OFF'}", "power": state})

@app.route("/api/display/schedule", methods=["POST"])
def api_display_schedule():
    auth_res = require_auth()
    if auth_res:
        return auth_res
    data = request.get_json(silent=True) or {}
    CONFIG["display_schedule"] = {
        "enabled": bool(data.get("enabled", False)),
        "sleep_time": data.get("sleep_time", "23:00"),
        "wake_time": data.get("wake_time", "07:00"),
    }
    save_config(CONFIG)
    return jsonify({"message": "Display power schedule updated", "schedule": CONFIG["display_schedule"]})

@app.route("/api/settings/cec", methods=["POST"])
def api_settings_cec():
    auth_res = require_auth()
    if auth_res:
        return auth_res
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", False))
    CONFIG["cec_enabled"] = enabled
    save_config(CONFIG)
    if enabled:
        threading.Thread(target=cec_listener_daemon, daemon=True).start()
    return jsonify({"message": f"HDMI-CEC remote {'enabled' if enabled else 'disabled'}", "cec_enabled": enabled})

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

## Web UI

INDEX_HTML = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover" />
  <title>PiMedia Remote</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    :root {
      --ease-spring: cubic-bezier(0.16, 1, 0.3, 1);
    }
    body {
      font-family: system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: #000000;
      color: #ffffff;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
    }
    .font-mono {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
    }
    .panel-surface {
      background: #1c1c1e;
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 24px;
    }
    .tile-surface {
      background: #2c2c2e;
      border: 1px solid rgba(255, 255, 255, 0.06);
      border-radius: 18px;
      transition: background-color 0.2s var(--ease-spring), transform 0.15s var(--ease-spring);
    }
    .tile-surface:active {
      background: #3a3a3c;
      transform: scale(0.98);
    }
    .btn-primary {
      background: #0a84ff;
      color: #ffffff;
      transition: all 0.2s var(--ease-spring);
    }
    .btn-primary:hover {
      background: #0071e3;
    }
    .btn-primary:active {
      transform: scale(0.97);
    }
    .btn-secondary {
      background: #2c2c2e;
      color: #ffffff;
      border: 1px solid rgba(255, 255, 255, 0.08);
      transition: all 0.2s var(--ease-spring);
    }
    .btn-secondary:hover {
      background: #3a3a3c;
    }
    .btn-secondary:active {
      transform: scale(0.96);
    }
    .btn-danger {
      background: rgba(255, 69, 58, 0.15);
      color: #ff453a;
      border: 1px solid rgba(255, 69, 58, 0.25);
      transition: all 0.2s var(--ease-spring);
    }
    .btn-danger:hover {
      background: rgba(255, 69, 58, 0.25);
    }
    .btn-danger:active {
      transform: scale(0.96);
    }
    /* Toggle Switch */
    .toggle-switch {
      position: relative;
      display: inline-block;
      width: 50px;
      height: 30px;
      flex-shrink: 0;
    }
    .toggle-switch input {
      opacity: 0;
      width: 0;
      height: 0;
    }
    .toggle-slider {
      position: absolute;
      cursor: pointer;
      top: 0; left: 0; right: 0; bottom: 0;
      background-color: #39393d;
      transition: background-color 0.25s var(--ease-spring);
      border-radius: 30px;
    }
    .toggle-slider:before {
      position: absolute;
      content: "";
      height: 26px;
      width: 26px;
      left: 2px;
      bottom: 2px;
      background-color: #ffffff;
      transition: transform 0.25s var(--ease-spring);
      border-radius: 50%;
      box-shadow: 0 2px 5px rgba(0,0,0,0.3);
    }
    input:checked + .toggle-slider {
      background-color: #30d158;
    }
    input:checked + .toggle-slider:before {
      transform: translateX(20px);
    }
    /* Native style slider */
    input[type=range] {
      -webkit-appearance: none;
      background: rgba(255, 255, 255, 0.18);
      border-radius: 9999px;
      height: 6px;
    }
    input[type=range]::-webkit-slider-thumb {
      -webkit-appearance: none;
      width: 18px;
      height: 18px;
      border-radius: 50%;
      background: #ffffff;
      box-shadow: 0 2px 6px rgba(0, 0, 0, 0.4);
      cursor: pointer;
      transition: transform 0.15s ease;
    }
    input[type=range]::-webkit-slider-thumb:hover {
      transform: scale(1.15);
    }
    .drop-active {
      border-color: #0a84ff !important;
      background: rgba(10, 132, 255, 0.08) !important;
    }
  </style>
</head>
<body class="min-h-screen flex flex-col antialiased selection:bg-blue-600 selection:text-white pb-12">

  <!-- Navigation Bar -->
  <header class="sticky top-0 z-40 px-4 py-3 sm:px-8 border-b border-white/10 bg-[#000000]/90 backdrop-blur-xl">
    <div class="max-w-5xl mx-auto flex items-center justify-between gap-4">
      
      <!-- Brand & Status -->
      <div class="flex items-center gap-3">
        <div class="w-8 h-8 rounded-xl bg-[#1c1c1e] border border-white/10 flex items-center justify-center text-white">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
        </div>
        <div>
          <div class="flex items-center gap-2">
            <span class="text-sm font-semibold tracking-tight text-white leading-none">PiMedia</span>
            <div id="statusDot" class="w-2 h-2 rounded-full bg-zinc-500"></div>
          </div>
          <p class="text-[11px] text-[#8e8e93] font-mono mt-0.5" id="hostIp">Connecting...</p>
        </div>
      </div>

      <!-- Segmented Desktop Navigation -->
      <div class="hidden sm:flex items-center bg-[#1c1c1e] p-1 rounded-full border border-white/10 text-xs">
        <button onclick="switchView('remote')" id="tabViewRemote" class="px-4 py-1.5 rounded-full bg-[#2c2c2e] text-white font-medium transition">Remote</button>
        <button onclick="switchView('media')" id="tabViewMedia" class="px-4 py-1.5 rounded-full text-[#8e8e93] hover:text-white transition">Library</button>
        <button onclick="switchView('stream')" id="tabViewStream" class="px-4 py-1.5 rounded-full text-[#8e8e93] hover:text-white transition">Stream</button>
        <button onclick="switchView('settings')" id="tabViewSettings" class="px-4 py-1.5 rounded-full text-[#8e8e93] hover:text-white transition">Settings</button>
      </div>

      <!-- Quick Actions -->
      <div class="flex items-center gap-2">
        <button onclick="toggleDisplayPower()" id="displayPowerBtn" class="p-2 rounded-full bg-[#1c1c1e] border border-white/10 text-[#8e8e93] hover:text-white transition" title="Toggle display power">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
        </button>
        <button onclick="refreshAll()" class="p-2 rounded-full bg-[#1c1c1e] border border-white/10 text-[#8e8e93] hover:text-white transition" title="Refresh">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
        </button>
      </div>
    </div>
  </header>

  <!-- Mobile Segmented Navigation -->
  <div class="sm:hidden px-4 pt-3">
    <div class="flex items-center justify-between bg-[#1c1c1e] p-1 rounded-full border border-white/10 text-xs w-full">
      <button onclick="switchView('remote')" id="tabViewRemoteMobile" class="flex-1 py-1.5 rounded-full bg-[#2c2c2e] text-white font-medium text-center transition">Remote</button>
      <button onclick="switchView('media')" id="tabViewMediaMobile" class="flex-1 py-1.5 rounded-full text-[#8e8e93] hover:text-white text-center transition">Library</button>
      <button onclick="switchView('stream')" id="tabViewStreamMobile" class="flex-1 py-1.5 rounded-full text-[#8e8e93] hover:text-white text-center transition">Stream</button>
      <button onclick="switchView('settings')" id="tabViewSettingsMobile" class="flex-1 py-1.5 rounded-full text-[#8e8e93] hover:text-white text-center transition">Settings</button>
    </div>
  </div>

  <main class="max-w-4xl mx-auto px-4 py-6 sm:px-6 flex-1 w-full space-y-6">

    <!-- VIEW 1: Remote (Media Player + Remote Clickpad) -->
    <div id="viewRemote" class="space-y-6">
      
      <!-- Media Player Card -->
      <section class="panel-surface p-6 sm:p-7 space-y-6">
        
        <!-- Media Header & Artwork -->
        <div class="flex items-center gap-4">
          <div class="w-16 h-16 sm:w-20 sm:h-20 rounded-2xl bg-[#2c2c2e] border border-white/10 flex items-center justify-center overflow-hidden flex-shrink-0" id="nowPlayingArt">
            <svg class="w-8 h-8 text-[#8e8e93]" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"/></svg>
          </div>
          <div class="flex-1 min-w-0">
            <p class="text-xs font-medium text-[#8e8e93] uppercase tracking-wider font-mono" id="playbackStateText">Idle</p>
            <h2 class="text-lg sm:text-xl font-semibold text-white tracking-tight truncate" id="nowPlayingText">No active media</h2>
            <p class="text-xs text-[#8e8e93] font-mono mt-0.5" id="storageSummary">Ready to play</p>
          </div>
          <div class="flex items-center gap-1.5">
            <button onclick="controlAction('fullscreen')" class="btn-secondary p-2.5 rounded-full" title="Toggle Fullscreen">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4"/></svg>
            </button>
          </div>
        </div>

        <!-- Scrubber Bar -->
        <div class="space-y-1.5">
          <div class="w-full bg-[#2c2c2e] h-2 rounded-full cursor-pointer relative overflow-hidden" id="scrubberTrack" onclick="handleScrub(event)">
            <div class="bg-white h-full transition-all duration-150 rounded-full" id="scrubberFill" style="width: 0%"></div>
          </div>
          <div class="flex items-center justify-between text-[11px] text-[#8e8e93] font-mono">
            <span id="posTime">0:00</span>
            <span id="durTime">0:00</span>
          </div>
        </div>

        <!-- Transport Controls -->
        <div class="flex items-center justify-center gap-4 sm:gap-6 pt-2">
          <button onclick="seekRelative(-10)" class="btn-secondary p-3 rounded-full" title="Skip backward 10s">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12.066 11.2a1 1 0 000 1.6l5.334 4A1 1 0 0019 16V8a1 1 0 00-1.6-.8l-5.334 4zM4.066 11.2a1 1 0 000 1.6l5.334 4A1 1 0 0011 16V8a1 1 0 00-1.6-.8l-5.334 4z"/></svg>
          </button>
          
          <button onclick="controlAction('prev')" class="btn-secondary p-3.5 rounded-full" title="Previous item">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M15 19l-7-7 7-7"/></svg>
          </button>

          <button id="mainPlayBtn" onclick="togglePlayPause()" class="w-14 h-14 rounded-full bg-white text-black flex items-center justify-center hover:scale-105 active:scale-95 transition shadow-lg" title="Play / Pause">
            <svg id="playIcon" class="w-6 h-6 fill-current ml-0.5" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
            <svg id="pauseIcon" class="w-6 h-6 fill-current hidden" viewBox="0 0 24 24"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>
          </button>

          <button onclick="controlAction('next')" class="btn-secondary p-3.5 rounded-full" title="Next item">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M9 5l7 7-7 7"/></svg>
          </button>

          <button onclick="seekRelative(10)" class="btn-secondary p-3 rounded-full" title="Skip forward 10s">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11.934 12.8a1 1 0 000-1.6l-5.334-4A1 1 0 005 8v8a1 1 0 001.6.8l5.334-4zM19.934 12.8a1 1 0 000-1.6l-5.334-4A1 1 0 0013 8v8a1 1 0 001.6.8l5.334-4z"/></svg>
          </button>
        </div>

        <!-- Volume & Interval Sliders -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-4 border-t border-white/10">
          
          <!-- Volume Capsule Slider -->
          <div class="tile-surface p-3.5 flex items-center gap-3">
            <svg class="w-4 h-4 text-[#8e8e93]" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z"/></svg>
            <input type="range" id="volumeSlider" min="0" max="100" value="100" onchange="updateVolume(this.value)" class="flex-1" />
            <svg class="w-4 h-4 text-[#8e8e93]" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.536 8.464a5 5 0 010 7.072m2.828-9.9a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z"/></svg>
          </div>

          <!-- Slideshow Speed Picker -->
          <div class="tile-surface p-3.5 flex items-center justify-between">
            <span class="text-xs text-[#8e8e93]">Slide Duration</span>
            <select id="slideDuration" onchange="updateDuration(this.value)" class="bg-[#1c1c1e] border border-white/10 text-xs text-white rounded-lg px-2.5 py-1 focus:outline-none">
              <option value="3">3s</option>
              <option value="5">5s</option>
              <option value="10">10s</option>
              <option value="15">15s</option>
              <option value="30">30s</option>
            </select>
          </div>
        </div>
      </section>

      <!-- Remote Clickpad -->
      <section class="panel-surface p-6 sm:p-7 flex flex-col items-center">
        <div class="text-center space-y-1 mb-6">
          <h3 class="text-sm font-semibold text-white">Remote Clickpad</h3>
          <p class="text-xs text-[#8e8e93]">Tactile navigation wheel for displays and projectors</p>
        </div>

        <!-- Circular D-Pad -->
        <div class="relative w-56 h-56 sm:w-64 sm:h-64 rounded-full bg-[#2c2c2e] border border-white/10 p-2 shadow-2xl flex items-center justify-center">
          
          <!-- Top Button (Volume Up) -->
          <button onclick="adjustVolume(5)" class="absolute top-2 left-1/2 -translate-x-1/2 w-16 h-12 flex items-center justify-center text-[#8e8e93] hover:text-white active:scale-95 transition" title="Volume Up">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 15l7-7 7 7"/></svg>
          </button>

          <!-- Bottom Button (Volume Down) -->
          <button onclick="adjustVolume(-5)" class="absolute bottom-2 left-1/2 -translate-x-1/2 w-16 h-12 flex items-center justify-center text-[#8e8e93] hover:text-white active:scale-95 transition" title="Volume Down">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7"/></svg>
          </button>

          <!-- Left Button (Previous Track / Rewind) -->
          <button onclick="controlAction('prev')" class="absolute left-2 top-1/2 -translate-y-1/2 w-12 h-16 flex items-center justify-center text-[#8e8e93] hover:text-white active:scale-95 transition" title="Previous Track">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 19l-7-7 7-7"/></svg>
          </button>

          <!-- Right Button (Next Track / Forward) -->
          <button onclick="controlAction('next')" class="absolute right-2 top-1/2 -translate-y-1/2 w-12 h-16 flex items-center justify-center text-[#8e8e93] hover:text-white active:scale-95 transition" title="Next Track">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7"/></svg>
          </button>

          <!-- Center Select Button -->
          <button onclick="togglePlayPause()" class="w-24 h-24 sm:w-28 sm:h-28 rounded-full bg-[#1c1c1e] border border-white/10 hover:bg-[#3a3a3c] active:scale-90 transition flex items-center justify-center text-white shadow-inner" title="Select / Play / Pause">
            <span class="text-xs font-semibold tracking-wider uppercase font-mono text-[#8e8e93]">Select</span>
          </button>
        </div>

        <!-- Auxiliary Buttons Cluster -->
        <div class="flex items-center gap-4 mt-6">
          <button onclick="controlAction('stop')" class="btn-danger px-5 py-2.5 rounded-full text-xs font-medium flex items-center gap-2" title="Stop Playback">
            <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M6 6h12v12H6z"/></svg>
            <span>Stop</span>
          </button>
          
          <button onclick="toggleDisplayPower()" class="btn-secondary px-5 py-2.5 rounded-full text-xs font-medium flex items-center gap-2" title="Power Display">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
            <span>Display</span>
          </button>
        </div>
      </section>

      <!-- Files Upload Zone -->
      <section>
        <div id="dropZone" class="border border-dashed border-white/15 hover:border-white/30 bg-[#1c1c1e] rounded-3xl p-8 text-center transition-all cursor-pointer flex flex-col items-center justify-center gap-3">
          <input type="file" id="fileInput" multiple accept="image/*,video/*,audio/*" class="hidden" />
          <div class="w-12 h-12 rounded-2xl bg-[#2c2c2e] border border-white/10 flex items-center justify-center text-[#0a84ff]">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/></svg>
          </div>
          <div>
            <p class="text-sm font-medium text-white">Drag files here or tap to upload</p>
            <p class="text-xs text-[#8e8e93] mt-0.5">MP4, MOV, MKV, WebM, JPG, PNG, WebP, MP3, FLAC, WAV</p>
          </div>
          <div id="uploadProgressContainer" class="w-full max-w-sm hidden mt-3">
            <div class="w-full bg-[#2c2c2e] rounded-full h-1.5 overflow-hidden">
              <div id="uploadProgressBar" class="bg-[#0a84ff] h-full transition-all duration-200" style="width: 0%"></div>
            </div>
            <p id="uploadStatusText" class="text-xs text-[#8e8e93] mt-2 font-mono">Uploading...</p>
          </div>
        </div>
      </section>
    </div>

    <!-- VIEW 2: Media Library Grid -->
    <div id="viewMedia" class="space-y-6 hidden">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div class="flex items-center gap-2.5">
          <h3 class="text-lg font-semibold text-white tracking-tight">Library</h3>
          <span class="text-xs px-2.5 py-0.5 rounded-full bg-[#1c1c1e] text-[#8e8e93] font-mono border border-white/10" id="fileCountBadge">0</span>
        </div>

        <div class="flex items-center gap-3">
          <div class="relative">
            <input type="text" id="searchInput" placeholder="Search..." oninput="filterMedia()" class="bg-[#1c1c1e] border border-white/10 text-white text-xs rounded-full px-3.5 py-2 pl-9 focus:outline-none focus:border-[#0a84ff] w-48" />
            <svg class="w-4 h-4 text-[#8e8e93] absolute left-3 top-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
          </div>
          <div class="flex bg-[#1c1c1e] p-1 rounded-full border border-white/10 text-xs">
            <button onclick="setTab('all')" id="tabAll" class="px-3 py-1 rounded-full bg-[#2c2c2e] text-white font-medium transition">All</button>
            <button onclick="setTab('video')" id="tabVideo" class="px-3 py-1 rounded-full text-[#8e8e93] hover:text-white transition">Videos</button>
            <button onclick="setTab('image')" id="tabImage" class="px-3 py-1 rounded-full text-[#8e8e93] hover:text-white transition">Photos</button>
            <button onclick="setTab('audio')" id="tabAudio" class="px-3 py-1 rounded-full text-[#8e8e93] hover:text-white transition">Audio</button>
          </div>
        </div>
      </div>

      <div id="mediaGrid" class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4"></div>
      
      <div id="emptyState" class="hidden py-16 text-center text-[#8e8e93] space-y-2">
        <p class="text-sm">No media in library</p>
        <p class="text-xs text-[#636366]">Drag media into the upload area above to start</p>
      </div>
    </div>

    <!-- VIEW 3: Web Stream -->
    <div id="viewStream" class="space-y-6 hidden">
      <section class="panel-surface p-6 sm:p-8 space-y-6">
        <div class="space-y-1">
          <h3 class="text-lg font-semibold text-white tracking-tight">Web Stream</h3>
          <p class="text-xs text-[#8e8e93]">Stream online video directly to the display using mpv and yt-dlp.</p>
        </div>

        <div class="space-y-4 pt-2">
          <div class="flex flex-col sm:flex-row items-center gap-3">
            <div class="relative w-full">
              <input type="url" id="streamUrlInput" placeholder="https://www.youtube.com/watch?v=... or direct video link" class="bg-[#2c2c2e] border border-white/10 text-xs text-white rounded-2xl px-4 py-3.5 pl-10 w-full focus:outline-none focus:border-[#0a84ff]" />
              <svg class="w-4 h-4 text-[#8e8e93] absolute left-3.5 top-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/></svg>
            </div>
            <button onclick="startStreamUrl()" class="btn-primary px-6 py-3.5 rounded-2xl text-xs font-semibold whitespace-nowrap w-full sm:w-auto flex items-center justify-center gap-2">
              <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
              <span>Play to Screen</span>
            </button>
          </div>

          <div class="flex flex-wrap gap-2 text-xs">
            <button onclick="fillPreset('https://www.youtube.com/watch?v=dQw4w9WgXcQ')" class="btn-secondary px-3 py-1.5 rounded-full text-[11px]">YouTube Sample</button>
            <button onclick="fillPreset('https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4')" class="btn-secondary px-3 py-1.5 rounded-full text-[11px]">Direct MP4 Sample</button>
          </div>
        </div>
      </section>
    </div>

    <!-- VIEW 4: Settings -->
    <div id="viewSettings" class="space-y-6 hidden">
      
      <!-- Group 1: Wireless Networks -->
      <div class="space-y-2">
        <span class="text-[11px] font-semibold tracking-wider text-[#8e8e93] uppercase font-mono px-4">Wireless Networks</span>
        <div class="panel-surface p-5 space-y-4">
          <div class="flex items-center justify-between">
            <div>
              <h4 class="text-sm font-semibold text-white">Nearby Wi-Fi</h4>
              <p class="text-xs text-[#8e8e93]">Scan and connect to local wireless networks</p>
            </div>
            <button onclick="scanWifi()" id="scanWifiBtn" class="btn-secondary px-4 py-2 rounded-xl text-xs font-medium flex items-center gap-1.5">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
              <span>Scan</span>
            </button>
          </div>
          <div id="wifiList" class="space-y-2">
            <div class="text-center py-4 text-[#8e8e93] text-xs font-mono">Select Scan to discover nearby Wi-Fi networks</div>
          </div>
        </div>
      </div>

      <!-- Group 2: Playback & Motion -->
      <div class="space-y-2">
        <span class="text-[11px] font-semibold tracking-wider text-[#8e8e93] uppercase font-mono px-4">Playback &amp; Hardware</span>
        <div class="panel-surface divide-y divide-white/10 overflow-hidden">
          
          <!-- Photo Drift Switch -->
          <div class="p-4 sm:p-5 flex items-center justify-between">
            <div class="space-y-0.5">
              <h4 class="text-sm font-semibold text-white">Photo Pan &amp; Zoom Drift</h4>
              <p class="text-xs text-[#8e8e93]">Slowly moves and scales photos during slideshow playback</p>
            </div>
            <label class="toggle-switch">
              <input type="checkbox" id="kenBurnsToggle" onchange="toggleKenBurns(this.checked)" />
              <span class="toggle-slider"></span>
            </label>
          </div>

          <!-- HDMI-CEC Switch -->
          <div class="p-4 sm:p-5 flex items-center justify-between">
            <div class="space-y-0.5">
              <h4 class="text-sm font-semibold text-white">HDMI-CEC Remote</h4>
              <p class="text-xs text-[#8e8e93]">Control playback and volume with your TV remote</p>
            </div>
            <label class="toggle-switch">
              <input type="checkbox" id="cecToggle" onchange="toggleCec(this.checked)" />
              <span class="toggle-slider"></span>
            </label>
          </div>

          <!-- Background Audio Track -->
          <div class="p-4 sm:p-5 space-y-2">
            <h4 class="text-sm font-semibold text-white">Background Audio</h4>
            <p class="text-xs text-[#8e8e93]">Loops selected audio track during photo slideshows</p>
            <select id="bgAudioSelect" onchange="updateBgAudio(this.value)" class="bg-[#2c2c2e] border border-white/10 text-xs text-white rounded-xl px-3.5 py-2.5 w-full focus:outline-none focus:border-[#0a84ff] mt-2">
              <option value="">None (Silent)</option>
            </select>
          </div>
        </div>
      </div>

      <!-- Group 3: Display Timer & PIN -->
      <div class="space-y-2">
        <span class="text-[11px] font-semibold tracking-wider text-[#8e8e93] uppercase font-mono px-4">Schedule &amp; Security</span>
        <div class="panel-surface divide-y divide-white/10 overflow-hidden">
          
          <!-- Timed Power Schedule -->
          <div class="p-4 sm:p-5 space-y-3">
            <div class="flex items-center justify-between">
              <div class="space-y-0.5">
                <h4 class="text-sm font-semibold text-white">Display Power Schedule</h4>
                <p class="text-xs text-[#8e8e93]">Automatically sleeps and wakes the HDMI output</p>
              </div>
              <label class="toggle-switch">
                <input type="checkbox" id="schedToggle" onchange="saveSchedule()" />
                <span class="toggle-slider"></span>
              </label>
            </div>
            <div class="grid grid-cols-2 gap-3 pt-2 text-xs">
              <div class="bg-[#2c2c2e] p-2.5 rounded-xl border border-white/5">
                <label class="text-[10px] text-[#8e8e93] uppercase font-mono block mb-1">Sleep Time</label>
                <input type="time" id="sleepTimeInput" value="23:00" onchange="saveSchedule()" class="bg-transparent text-white text-xs w-full focus:outline-none" />
              </div>
              <div class="bg-[#2c2c2e] p-2.5 rounded-xl border border-white/5">
                <label class="text-[10px] text-[#8e8e93] uppercase font-mono block mb-1">Wake Time</label>
                <input type="time" id="wakeTimeInput" value="07:00" onchange="saveSchedule()" class="bg-transparent text-white text-xs w-full focus:outline-none" />
              </div>
            </div>
          </div>

          <!-- PIN Access -->
          <div class="p-4 sm:p-5 space-y-3">
            <div class="space-y-0.5">
              <h4 class="text-sm font-semibold text-white">Access PIN</h4>
              <p class="text-xs text-[#8e8e93]">Restrict remote playback and file uploads</p>
            </div>
            <div class="flex items-center gap-2">
              <input type="password" id="pinInput" placeholder="Leave empty for open access" class="bg-[#2c2c2e] border border-white/10 text-xs text-white rounded-xl px-3.5 py-2.5 w-full focus:outline-none focus:border-[#0a84ff]" />
              <button onclick="savePin()" class="btn-primary px-4 py-2.5 rounded-xl text-xs font-medium">Save</button>
            </div>
          </div>
        </div>
      </div>

      <!-- Group 4: Diagnostics -->
      <div class="space-y-2">
        <span class="text-[11px] font-semibold tracking-wider text-[#8e8e93] uppercase font-mono px-4">System Diagnostics</span>
        <div class="panel-surface p-5 space-y-3 text-xs font-mono">
          <div class="flex justify-between py-1 border-b border-white/5">
            <span class="text-[#8e8e93]">IP Address</span>
            <span class="text-white" id="diagIp">127.0.0.1</span>
          </div>
          <div class="flex justify-between py-1 border-b border-white/5">
            <span class="text-[#8e8e93]">CPU Temperature</span>
            <span class="text-white" id="diagTemp">-- &deg;C</span>
          </div>
          <div class="flex justify-between py-1">
            <span class="text-[#8e8e93]">Storage Free</span>
            <span class="text-white" id="diagDisk">-- GB</span>
          </div>
        </div>
      </div>

    </div>

  </main>

  <footer class="border-t border-white/10 py-4 px-6 text-center text-[11px] text-[#636366] font-mono">
    PiMedia
  </footer>

  <!-- Connect Wi-Fi Modal -->
  <div id="wifiModal" class="fixed inset-0 z-50 bg-black/80 backdrop-blur-md flex items-center justify-center p-4 hidden">
    <div class="panel-surface p-6 sm:p-7 max-w-sm w-full space-y-4">
      <div class="space-y-1">
        <h4 class="text-base font-semibold text-white" id="modalSsidTitle">Connect to Wi-Fi</h4>
        <p class="text-xs text-[#8e8e93]">Enter network password to join.</p>
      </div>
      <input type="password" id="modalWifiPassword" placeholder="Password" class="bg-[#2c2c2e] border border-white/10 text-xs text-white rounded-xl px-3.5 py-3 w-full focus:outline-none focus:border-[#0a84ff]" />
      <div class="flex items-center justify-end gap-2.5 pt-2">
        <button onclick="closeWifiModal()" class="btn-secondary px-4 py-2 rounded-xl text-xs">Cancel</button>
        <button onclick="submitWifiConnect()" class="btn-primary px-5 py-2 rounded-xl text-xs font-medium">Join</button>
      </div>
    </div>
  </div>

  <script>
    let mediaItems = [];
    let activeTab = 'all';
    let targetSsid = '';
    let currentPowerState = true;
    let isPlaying = false;
    let currentDuration = 0;
    let currentPosition = 0;

    function switchView(viewName) {
      const views = ['Remote', 'Media', 'Stream', 'Settings'];
      views.forEach(v => {
        const el = document.getElementById('view' + v);
        const tab = document.getElementById('tabView' + v);
        const tabMobile = document.getElementById('tabView' + v + 'Mobile');
        
        if (v.toLowerCase() === viewName) {
          if (el) el.classList.remove('hidden');
          if (tab) tab.className = 'px-4 py-1.5 rounded-full bg-[#2c2c2e] text-white font-medium transition';
          if (tabMobile) tabMobile.className = 'flex-1 py-1.5 rounded-full bg-[#2c2c2e] text-white font-medium text-center transition';
        } else {
          if (el) el.classList.add('hidden');
          if (tab) tab.className = 'px-4 py-1.5 rounded-full text-[#8e8e93] hover:text-white transition';
          if (tabMobile) tabMobile.className = 'flex-1 py-1.5 rounded-full text-[#8e8e93] hover:text-white text-center transition';
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
        
        const diagIp = document.getElementById('diagIp');
        if (diagIp) diagIp.textContent = data.system.ip;

        const diagTemp = document.getElementById('diagTemp');
        if (diagTemp) diagTemp.textContent = (data.system.cpu_temp !== null) ? `${data.system.cpu_temp} °C` : 'N/A';

        const diagDisk = document.getElementById('diagDisk');
        if (diagDisk) diagDisk.textContent = `${data.system.disk_free_gb} GB free (${data.system.disk_used_percent}% used)`;

        const storageSummary = document.getElementById('storageSummary');
        if (storageSummary) storageSummary.textContent = `${data.system.media_count} items • ${data.system.disk_free_gb} GB free`;

        const dot = document.getElementById('statusDot');
        const stateText = document.getElementById('playbackStateText');
        const nowPlaying = document.getElementById('nowPlayingText');
        const playIcon = document.getElementById('playIcon');
        const pauseIcon = document.getElementById('pauseIcon');

        isPlaying = data.playback.is_running && !data.playback.paused;
        currentDuration = data.playback.duration || 0;
        currentPosition = data.playback.position || 0;

        if (data.playback.is_running) {
          dot.className = 'w-2 h-2 rounded-full bg-[#30d158] animate-pulse';
          stateText.textContent = data.playback.paused ? 'Paused' : 'Playing';
          nowPlaying.textContent = data.playback.filename || 'Active Playlist';
          if (isPlaying) {
            playIcon.classList.add('hidden');
            pauseIcon.classList.remove('hidden');
          } else {
            playIcon.classList.remove('hidden');
            pauseIcon.classList.add('hidden');
          }
        } else {
          dot.className = 'w-2 h-2 rounded-full bg-zinc-500';
          stateText.textContent = 'Idle';
          nowPlaying.textContent = 'No active media';
          playIcon.classList.remove('hidden');
          pauseIcon.classList.add('hidden');
        }

        // Update progress bar
        document.getElementById('posTime').textContent = formatTime(currentPosition);
        document.getElementById('durTime').textContent = formatTime(currentDuration);
        const percent = currentDuration > 0 ? (currentPosition / currentDuration) * 100 : 0;
        document.getElementById('scrubberFill').style.width = `${percent}%`;

        // Update settings controls
        const durSelect = document.getElementById('slideDuration');
        if (durSelect && data.config.image_duration) durSelect.value = String(data.config.image_duration);

        const kbToggle = document.getElementById('kenBurnsToggle');
        if (kbToggle) kbToggle.checked = Boolean(data.config.ken_burns);

        const cecToggle = document.getElementById('cecToggle');
        if (cecToggle) cecToggle.checked = Boolean(data.config.cec_enabled);

        const sched = data.config.display_schedule || {};
        const schedToggle = document.getElementById('schedToggle');
        if (schedToggle) schedToggle.checked = Boolean(sched.enabled);
        if (sched.sleep_time) document.getElementById('sleepTimeInput').value = sched.sleep_time;
        if (sched.wake_time) document.getElementById('wakeTimeInput').value = sched.wake_time;

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
        updateBgAudioOptions();
      } catch (err) {
        console.error(err);
      }
    }

    function updateBgAudioOptions() {
      const select = document.getElementById('bgAudioSelect');
      if (!select) return;
      const audioFiles = mediaItems.filter(m => m.type === 'audio');
      const currentVal = select.value;
      select.innerHTML = '<option value="">None (Silent)</option>' + audioFiles.map(a => `<option value="${a.name}">${a.name}</option>`).join('');
      select.value = currentVal;
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
        <div class="group panel-surface p-2.5 overflow-hidden flex flex-col justify-between">
          <div class="relative w-full aspect-video bg-[#2c2c2e] rounded-xl flex items-center justify-center overflow-hidden">
            ${item.type === 'video' 
              ? `<video src="/media/${encodeURIComponent(item.name)}" class="w-full h-full object-cover" preload="metadata"></video>
                 <div class="absolute inset-0 bg-black/40 flex items-center justify-center pointer-events-none group-hover:bg-black/10 transition">
                   <div class="p-2 rounded-full bg-black/70 text-white backdrop-blur">
                     <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
                   </div>
                 </div>`
              : item.type === 'audio'
              ? `<div class="w-full h-full flex flex-col items-center justify-center text-[#0a84ff]">
                   <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3"/></svg>
                 </div>`
              : `<img src="/media/${encodeURIComponent(item.name)}" class="w-full h-full object-cover" loading="lazy" />`
            }
            <button onclick="playDirect('${item.name}')" class="absolute inset-0 z-10 opacity-0 group-hover:opacity-100 bg-[#0a84ff]/40 backdrop-blur-xs flex items-center justify-center transition text-xs font-semibold text-white gap-1.5">
              <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
              <span>Play</span>
            </button>
            <button onclick="deleteFile('${item.name}')" class="absolute top-1.5 right-1.5 z-20 p-1.5 rounded-full bg-black/60 hover:bg-[#ff453a] text-[#8e8e93] hover:text-white transition backdrop-blur opacity-0 group-hover:opacity-100" title="Delete">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
            </button>
          </div>
          <div class="pt-2 px-1 flex flex-col justify-between flex-1">
            <p class="text-xs font-medium text-white truncate" title="${item.name}">${item.name}</p>
            <div class="flex items-center justify-between text-[10px] text-[#8e8e93] font-mono mt-1">
              <span>${formatBytes(item.size)}</span>
              <span class="uppercase tracking-wider text-[#636366] font-semibold">${item.extension}</span>
            </div>
          </div>
        </div>
      `).join('');
    }

    function setTab(tab) {
      activeTab = tab;
      ['All', 'Video', 'Image', 'Audio'].forEach(t => {
        const el = document.getElementById('tab' + t);
        if (el) {
          if (t.toLowerCase() === tab) {
            el.className = 'px-3 py-1 rounded-full bg-[#2c2c2e] text-white font-medium transition';
          } else {
            el.className = 'px-3 py-1 rounded-full text-[#8e8e93] hover:text-white transition';
          }
        }
      });
      renderGrid();
    }

    function filterMedia() {
      renderGrid();
    }

    async function togglePlayPause() {
      await fetch('/api/playback/pause', { method: 'POST' });
      fetchStatus();
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

    async function seekRelative(secs) {
      try {
        await fetch('/api/playback/seek', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ seconds: secs })
        });
        fetchStatus();
      } catch (err) {
        console.error(err);
      }
    }

    function handleScrub(e) {
      if (!currentDuration) return;
      const rect = e.currentTarget.getBoundingClientRect();
      const posRatio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      const targetPos = posRatio * currentDuration;
      fetch('/api/playback/seek', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ position: targetPos })
      }).then(() => fetchStatus());
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

    function fillPreset(url) {
      document.getElementById('streamUrlInput').value = url;
    }

    async function startStreamUrl() {
      const url = document.getElementById('streamUrlInput').value.trim();
      if (!url) return;
      try {
        await fetch('/api/playback/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url })
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

    function adjustVolume(delta) {
      const slider = document.getElementById('volumeSlider');
      let val = parseInt(slider.value) + delta;
      val = Math.max(0, Math.min(100, val));
      slider.value = val;
      updateVolume(val);
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

    async function toggleKenBurns(enabled) {
      try {
        await fetch('/api/playback/kenburns', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled })
        });
      } catch (err) {
        console.error(err);
      }
    }

    async function toggleCec(enabled) {
      try {
        await fetch('/api/settings/cec', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled })
        });
      } catch (err) {
        console.error(err);
      }
    }

    async function updateBgAudio(val) {
      try {
        await fetch('/api/playback/background_audio', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ audio_file: val })
        });
      } catch (err) {
        console.error(err);
      }
    }

    async function toggleDisplayPower() {
      currentPowerState = !currentPowerState;
      try {
        await fetch('/api/display/power', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ power: currentPowerState })
        });
      } catch (err) {
        console.error(err);
      }
    }

    async function saveSchedule() {
      const enabled = document.getElementById('schedToggle').checked;
      const sleep_time = document.getElementById('sleepTimeInput').value;
      const wake_time = document.getElementById('wakeTimeInput').value;
      try {
        await fetch('/api/display/schedule', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled, sleep_time, wake_time })
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

    // Drag and Drop
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

    // Wi-Fi Scanner
    async function scanWifi() {
      const list = document.getElementById('wifiList');
      const btn = document.getElementById('scanWifiBtn');
      list.innerHTML = '<div class="text-center py-4 text-[#8e8e93] text-xs font-mono animate-pulse">Scanning nearby networks...</div>';
      btn.disabled = true;

      try {
        const res = await fetch('/api/wifi/scan');
        const data = await res.json();
        btn.disabled = false;

        if (!data || data.length === 0) {
          list.innerHTML = '<div class="text-center py-4 text-[#8e8e93] text-xs font-mono">No Wi-Fi networks detected</div>';
          return;
        }

        list.innerHTML = data.map(net => `
          <div class="tile-surface p-3.5 flex items-center justify-between">
            <div class="flex items-center gap-3">
              <div class="w-8 h-8 rounded-xl ${net.connected ? 'bg-[#30d158]/20 text-[#30d158]' : 'bg-[#1c1c1e] text-[#8e8e93]'} flex items-center justify-center">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.111 16.404a5.5 5.5 0 017.778 0M12 20h.01m-7.08-7.071c3.904-3.905 10.236-3.905 14.141 0M1.394 9.393c5.857-5.857 15.355-5.857 21.213 0"/></svg>
              </div>
              <div>
                <p class="text-xs font-semibold text-white">${net.ssid}</p>
                <p class="text-[10px] text-[#8e8e93] font-mono">${net.security} • ${net.signal}% signal ${net.connected ? '• Connected' : ''}</p>
              </div>
            </div>
            ${net.connected 
              ? `<span class="px-3 py-1 rounded-full bg-[#30d158]/15 text-[#30d158] text-[11px] font-medium border border-[#30d158]/30">Connected</span>`
              : `<button onclick="openWifiModal('${net.ssid}')" class="btn-primary px-3.5 py-1.5 rounded-xl text-xs font-medium">Join</button>`
            }
          </div>
        `).join('');
      } catch (err) {
        btn.disabled = false;
        list.innerHTML = '<div class="text-center py-4 text-[#ff453a] text-xs font-mono">Failed to scan networks</div>';
      }
    }

    function openWifiModal(ssid) {
      targetSsid = ssid;
      document.getElementById('modalSsidTitle').textContent = `Join "${ssid}"`;
      document.getElementById('modalWifiPassword').value = '';
      document.getElementById('wifiModal').classList.remove('hidden');
    }

    function closeWifiModal() {
      document.getElementById('wifiModal').classList.add('hidden');
    }

    async function submitWifiConnect() {
      const pwd = document.getElementById('modalWifiPassword').value;
      closeWifiModal();
      try {
        const res = await fetch('/api/wifi/connect', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ssid: targetSsid, password: pwd })
        });
        scanWifi();
      } catch (err) {
        console.error(err);
      }
    }

    async function savePin() {
      const pin = document.getElementById('pinInput').value.trim();
      try {
        await fetch('/api/settings/pin', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pin })
        });
      } catch (err) {
        console.error(err);
      }
    }

    function refreshAll() {
      fetchStatus();
      fetchFiles();
    }

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
    print(f"Starting PiMedia on http://0.0.0.0:5000 (Media: {MEDIA_DIR})")
    app.run(host="0.0.0.0", port=5000, debug=False)
