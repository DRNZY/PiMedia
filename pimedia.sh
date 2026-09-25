#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$DIR/.venv/bin/python"

if [ ! -f "$PYTHON" ]; then
    if command -v python3 &>/dev/null; then
        PYTHON="python3"
    else
        echo "Python 3 is required."
        exit 1
    fi
fi

case "$1" in
    run)
        echo "Starting PiMedia in foreground..."
        exec "$PYTHON" "$DIR/server.py"
        ;;
    start)
        if command -v systemctl &>/dev/null && systemctl list-unit-files | grep -q pimedia.service; then
            sudo systemctl start pimedia
            echo "Started pimedia.service"
        else
            nohup "$PYTHON" "$DIR/server.py" > /tmp/pimedia.log 2>&1 &
            echo "PiMedia started in background (PID: $!). Logs at /tmp/pimedia.log"
        fi
        ;;
    stop)
        if command -v systemctl &>/dev/null && systemctl is-active --quiet pimedia 2>/dev/null; then
            sudo systemctl stop pimedia
            echo "Stopped pimedia.service"
        else
            pkill -f "python.*server.py" || true
            pkill -f "mpv --input-ipc-server=/tmp/mpv-socket" || true
            echo "PiMedia stopped."
        fi
        ;;
    status)
        if command -v systemctl &>/dev/null && systemctl list-unit-files | grep -q pimedia.service; then
            systemctl status pimedia
        else
            pgrep -fl "server.py" || echo "PiMedia is not running."
        fi
        ;;
    wifi-scan)
        if command -v nmcli &>/dev/null; then
            nmcli -t -f SSID,SIGNAL,SECURITY dev wifi list --rescan yes
        else
            echo "nmcli not available"
        fi
        ;;
    wifi-connect)
        if [ -z "$2" ]; then
            echo "Usage: ./pimedia.sh wifi-connect <SSID> [PASSWORD]"
            exit 1
        fi
        if [ -n "$3" ]; then
            nmcli dev wifi connect "$2" password "$3"
        else
            nmcli dev wifi connect "$2"
        fi
        ;;
    *)
        echo "PiMedia Control Utility"
        echo ""
        echo "Usage: ./pimedia.sh [run|start|stop|status|wifi-scan|wifi-connect]"
        echo ""
        echo "Commands:"
        echo "  run           Run PiMedia in foreground (ideal for testing)"
        echo "  start         Start PiMedia background service"
        echo "  stop          Stop PiMedia and active MPV playback"
        echo "  status        Check server and process status"
        echo "  wifi-scan     Scan for available wireless networks"
        echo "  wifi-connect  Connect to a Wi-Fi network"
        exit 0
        ;;
esac
