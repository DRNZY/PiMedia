from flask import Flask, request, redirect, jsonify, send_from_directory, render_template_string
import os, subprocess, json, socket, time
from werkzeug.utils import secure_filename

app = Flask(__name__)
MEDIA = "/home/pi/media"
ALLOWED = {'jpg','jpeg','png','gif','mp4','mov','avi','mkv','webm'}
SOCKET = "/tmp/mpv-socket"
PID_FILE = "/tmp/mpv.pid"
PLAYLIST = "/tmp/playlist.m3u"
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

# Auth token loaded from environment; if unset, auth is disabled
AUTH_TOKEN = os.environ.get('MEDIA_AUTH_TOKEN')

def require_auth():
    """Check for a valid token in the 'X-Auth-Token' header or 'token' query param."""
    if AUTH_TOKEN is None:
        return None  # auth disabled
    token = request.headers.get('X-Auth-Token') or request.args.get('token')
    if token != AUTH_TOKEN:
        return ('Unauthorized', 401)

def allowed(f):
    return '.' in f and f.rsplit('.',1)[1].lower() in ALLOWED

def get_files():
    if not os.path.exists(MEDIA): return []
    files = []
    for f in sorted(os.listdir(MEDIA)):
        ext = f.rsplit('.',1)[1].lower() if '.' in f else ''
        if ext in ALLOWED:
            files.append({
                'name': f,
                'type': 'video' if ext in {'mp4','mov','avi','mkv','webm'} else 'image',
                'size': os.path.getsize(os.path.join(MEDIA, f))
            })
    return files

def is_running():
    """Check if mpv is alive by reading the pid and verifying /proc/<pid>/exe."""
    if not os.path.exists(PID_FILE):
        return False
    try:
        with open(PID_FILE) as fh:
            pid = fh.read().strip()
        if not pid:
            return False
        # Verify the process exists and is mpv
        exe = os.readlink('/proc/{}/exe'.format(pid))
        if 'mpv' in exe:
            return True
    except (FileNotFoundError, ProcessLookupError, OSError):
        pass
    return False

def kill_mpv():
    subprocess.run(['pkill','-f','mpv'], stderr=subprocess.DEVNULL)
    subprocess.run(['killall','mpv'], stderr=subprocess.DEVNULL)

def send_mpv_command(command):
    """Send a JSON command to the mpv IPC socket and return the response."""
    if not os.path.exists(SOCKET):
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect(SOCKET)
            payload = json.dumps(command) + '\n'
            sock.sendall(payload.encode())
            # Read response (mpv replies with one json line per command)
            data = b''
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b'\n' in chunk:
                    break
            return json.loads(data.decode()) if data else None
    except (socket.error, json.JSONDecodeError, OSError):
        return None

def run_mpv_command(cmd):
    env = os.environ.copy()
    env['DISPLAY'] = ':0'
    if cmd == 'play':
        # Kill existing
        kill_mpv()
        # Clean up stale state files
        for p in [PID_FILE, PLAYLIST, SOCKET]:
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
        # Ensure media directory exists
        if not os.path.exists(MEDIA):
            return 'Media directory niet gevonden', 500
        # Build playlist matching gallery order (same logic as get_files)
        files = []
        for f in sorted(os.listdir(MEDIA)):
            ext = f.rsplit('.',1)[1].lower() if '.' in f else ''
            if ext in ALLOWED:
                files.append(os.path.join(MEDIA, f))
        if not files:
            return 'Geen mediabestanden', 400
        with open(PLAYLIST, 'w') as f:
            for path in files:
                f.write(path + '\n')
        # Start MPV with IPC
        proc = subprocess.Popen([
            'mpv','--fs','--loop-playlist=yes','--playlist=' + PLAYLIST,
            '--input-ipc-server=' + SOCKET, '--image-display-duration=10',
            '--no-osc', '--no-osd-bar', '--quiet'
        ], env=env)
        # Write pid file
        with open(PID_FILE, 'w') as f:
            f.write(str(proc.pid))
        return 'Gestart'
    elif cmd == 'stop':
        kill_mpv()
        # Remove stale state files
        for p in [PID_FILE, PLAYLIST, SOCKET]:
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
        return 'Gestopt'
    elif cmd == 'next':
        resp = send_mpv_command({"command": ["playlist-next"]})
        if resp is None:
            return 'Niet verbonden', 503
        return 'Volgende'
    return 'OK'

@app.route('/')
def home():
    files = get_files()
    files_json = json.dumps(files)
    running = is_running()
    html = '''<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Media Player</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0a0a0a; color: #fff; font-family: -apple-system, sans-serif; padding: 10px; }
h1 { text-align: center; padding: 15px; font-size: 24px; }
.upload-zone { border: 2px dashed #333; border-radius: 12px; padding: 30px; text-align: center; margin: 10px 0; background: #111; }
.upload-zone.dragover { border-color: #667eea; background: #1a1a2e; }
input[type=file] { display: none; }
.btn { padding: 15px 30px; border: none; border-radius: 8px; font-size: 16px; cursor: pointer; margin: 5px; }
.btn-primary { background: #667eea; color: white; }
.btn-danger { background: #dc3545; color: white; }
.btn-success { background: #28a745; color: white; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.controls { display: flex; justify-content: center; flex-wrap: wrap; margin: 15px 0; }
.status { text-align: center; padding: 15px; background: #1a1a2e; border-radius: 12px; margin: 10px 0; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; margin-top: 15px; }
.file-card { background: #1a1a2e; border-radius: 12px; overflow: hidden; position: relative; }
.file-card img, .file-card video { width: 100%; height: 120px; object-fit: cover; display: block; }
.file-info { padding: 10px; font-size: 12px; }
.file-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.delete-btn { position: absolute; top: 5px; right: 5px; background: #dc3545; color: white; border: none; width: 30px; height: 30px; border-radius: 50%; cursor: pointer; font-size: 16px; }
.progress { width: 100%; height: 4px; background: #333; border-radius: 2px; margin-top: 10px; display: none; }
.progress-bar { height: 100%; background: #667eea; width: 0%; border-radius: 2px; transition: width 0.3s; }
</style>
</head>
<body>
<h1>\U0001f3ac Media Player</h1>
<div class="upload-zone" id="dropZone">
    <p>Sleep bestanden hier of klik om te uploaden</p>
    <input type="file" id="fileInput" multiple accept="image/*,video/*">
    <button class="btn btn-primary" onclick="document.getElementById('fileInput').click()">\U0001f4c1 Kies bestanden</button>
    <div class="progress" id="progress"><div class="progress-bar" id="progressBar"></div></div>
</div>
<div class="controls">
    <button class="btn btn-success" id="playBtn" onclick="control('play')">▶ Start</button>
    <button class="btn btn-danger" id="stopBtn" onclick="control('stop')">⏹ Stop</button>
    <button class="btn btn-primary" id="nextBtn" onclick="control('next')">⏭ Volgende</button>
</div>
<div class="status" id="status">''' + ('Loopt' if running else 'Gestopt') + '''</div>
<div class="grid" id="grid"></div>
<script>
const files = '''+files_json+''';
const grid = document.getElementById('grid');
files.forEach(f => {
    const card = document.createElement('div');
    card.className = 'file-card';
    const preview = f.type === 'video'
        ? '<video src="/media/'+f.name+'" preload="metadata" onclick="this.play()"></video>'
        : '<img src="/media/'+f.name+'" loading="lazy">';
    card.innerHTML = preview + '<div class="file-info"><div class="file-name">'+f.name+'</div><div>'+(f.size/1024/1024).toFixed(1)+' MB</div></div><button class="delete-btn" onclick="deleteFile(\\''+f.name+'\\')">×</button>';
    grid.appendChild(card);
});
function deleteFile(name) {
    if(!confirm('Verwijder '+name+'?')) return;
    fetch('/delete/'+encodeURIComponent(name), {method:'POST'}).then(r => {
        if(!r.ok) alert('Fout bij verwijderen');
        location.reload();
    });
}
function control(action) {
    fetch('/'+action, {method:'POST'}).then(r => r.text()).then(t => {
        document.getElementById('status').textContent = t;
        if(action === 'play') {
            document.getElementById('playBtn').disabled = true;
            document.getElementById('stopBtn').disabled = false;
        } else if (action === 'stop') {
            document.getElementById('playBtn').disabled = false;
            document.getElementById('stopBtn').disabled = true;
        }
    }).catch(() => {
        document.getElementById('status').textContent = 'Fout bij ' + action;
    });
}
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
dropZone.ondragover = e => { e.preventDefault(); dropZone.classList.add('dragover'); };
dropZone.ondragleave = () => dropZone.classList.remove('dragover');
dropZone.ondrop = e => { e.preventDefault(); dropZone.classList.remove('dragover'); uploadFiles(e.dataTransfer.files); };
fileInput.onchange = e => uploadFiles(e.target.files);
function uploadFiles(files) {
    const progress = document.getElementById('progress');
    const bar = document.getElementById('progressBar');
    progress.style.display = 'block';
    let uploaded = 0;
    Array.from(files).forEach(file => {
        const form = new FormData();
        form.append('file', file);
        fetch('/upload', {method:'POST', body:form}).then(r => {
            if(!r.ok) return r.text().then(t => { alert('Fout: '+t); });
            uploaded++;
            bar.style.width = (uploaded/files.length*100)+'%';
            if(uploaded === files.length) setTimeout(() => location.reload(), 500);
        }).catch(() => {
            alert('Upload mislukt voor ' + file.name);
            uploaded++;
            bar.style.width = (uploaded/files.length*100)+'%';
        });
    });
}
</script>
</body>
</html>'''
    return html

@app.route('/media/<path:filename>')
def media(filename):
    return send_from_directory(MEDIA, filename)

@app.route('/upload', methods=['POST'])
def upload():
    auth_err = require_auth()
    if auth_err:
        return auth_err
    if 'file' not in request.files:
        return 'No file', 400
    f = request.files['file']
    if not f.filename:
        return 'Lege bestandsnaam', 400
    if not allowed(f.filename):
        return 'Bestandstype niet toegestaan', 400
    safe_name = secure_filename(f.filename)
    if not safe_name:
        return 'Geen veilige bestandsnaam overgebleven na filtering', 400
    dest = os.path.join(MEDIA, safe_name)
    # De-duplicate: if file already exists, append timestamp
    if os.path.exists(dest):
        base, dot, ext = safe_name.rpartition('.')
        safe_name = '{}_{}.{}'.format(base, int(time.time()), ext) if base else '{}_{}'.format(safe_name, int(time.time()))
        dest = os.path.join(MEDIA, safe_name)
    os.makedirs(MEDIA, exist_ok=True)
    try:
        f.save(dest)
    except Exception as e:
        return 'Opslaan mislukt: {}'.format(e), 500
    return safe_name, 200

@app.route('/delete/<path:filename>', methods=['POST'])
def delete(filename):
    auth_err = require_auth()
    if auth_err:
        return auth_err
    safe_name = secure_filename(filename)
    if not safe_name:
        return 'Ongeldige bestandsnaam', 400
    path = os.path.join(MEDIA, safe_name)
    if os.path.exists(path):
        os.remove(path)
    return redirect('/')

@app.route('/play', methods=['POST'])
def play():
    auth_err = require_auth()
    if auth_err:
        return auth_err
    return run_mpv_command('play')

@app.route('/stop', methods=['POST'])
def stop():
    auth_err = require_auth()
    if auth_err:
        return auth_err
    return run_mpv_command('stop')

@app.route('/next', methods=['POST'])
def next_track():
    auth_err = require_auth()
    if auth_err:
        return auth_err
    return run_mpv_command('next')

@app.route('/files')
def files():
    return jsonify(get_files())

@app.errorhandler(413)
def request_entity_too_large(e):
    return 'Bestand te groot (max {} MB)'.format(MAX_FILE_SIZE // (1024*1024)), 413

if __name__ == '__main__':
    # Clean up stale state from previous runs
    for stale in [PLAYLIST, PID_FILE, SOCKET]:
        try:
            os.remove(stale)
        except FileNotFoundError:
            pass
    app.run(host='0.0.0.0', port=5000)
