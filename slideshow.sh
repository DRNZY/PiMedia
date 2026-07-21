#!/bin/bash
MEDIA="/home/pi/media"
SOCKET="/tmp/mpv-socket"
PIDFILE="/tmp/slideshow.pid"
STOPFILE="/tmp/slideshow.stop"

cleanup() {
    killall mpv 2>/dev/null
    rm -f "$SOCKET" "$PIDFILE" "$STOPFILE"
}

next_slide() {
    [ -S "$SOCKET" ] && echo '{ "command": ["playlist-next"] }' | socat - UNIX-CONNECT:"$SOCKET" 2>/dev/null
}

case "$1" in
    stop)
        cleanup
        exit 0
        ;;
    next)
        next_slide
        exit 0
        ;;
esac

cleanup
echo $$ > "$PIDFILE"

cd "$MEDIA"
# Maak playlist
ls -1 *.mp4 *.mov *.avi *.mkv *.webm 2>/dev/null > /tmp/playlist.m3u
ls -1 *.jpg *.jpeg *.png *.gif 2>/dev/null >> /tmp/playlist.m3u

# Start MPV met IPC
mpv --fs --loop-file=no --loop-playlist=yes --playlist=/tmp/playlist.m3u --input-ipc-server="$SOCKET" --image-display-duration=10 --no-osd-bar --no-osc &
PID=$!

while true; do
    sleep 0.5
    [ -f "$STOPFILE" ] && cleanup && break
    kill -0 $PID 2>/dev/null || break
done
