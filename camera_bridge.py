import base64
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler

MODEL = os.environ.get('CAMERA_MODEL', 'Qwen3.5-9B-4bit')
# 127.0.0.1 is the loopback address. The SSH tunnel makes oMLX available there.
UPSTREAM = os.environ.get('OMLX_URL', 'http://127.0.0.1:8000/v1/chat/completions')
TOKEN_FILE = Path(__file__).resolve().with_name('.camera-token')
if not TOKEN_FILE.exists():
    with open(TOKEN_FILE, 'x', opener=lambda p, f: os.open(p, f, 0o600)) as f:
        f.write(secrets.token_urlsafe(32))
TOKEN = TOKEN_FILE.read_text().strip()
if not TOKEN:
    raise SystemExit('Leere Schluesseldatei: ' + str(TOKEN_FILE))
BUSY = threading.Lock()
MAX_IMAGE = 5 * 1024 * 1024
OPENER = build_opener(ProxyHandler({}))


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def reply(self, status, message):
        data = (message + '\n').encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path == '/health':
            self.reply(200, 'Bilddienst erreichbar (oMLX nicht mitgeprueft).')
        else:
            self.reply(404, 'Nicht gefunden.')

    def do_POST(self):
        if self.path != '/describe':
            return self.reply(404, 'Nicht gefunden.')
        auth = self.headers.get('Authorization', '').encode()
        if not hmac.compare_digest(auth, ('Bearer ' + TOKEN).encode()):
            return self.reply(401, 'Zugriffsschluessel fehlt oder ist falsch.')
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            return self.reply(400, 'Ungueltige Dateigroesse.')
        if self.headers.get('Transfer-Encoding') or not 0 < length <= MAX_IMAGE:
            return self.reply(413, 'JPEG oder PNG bis maximal 5 MB senden.')
        if not BUSY.acquire(blocking=False):
            return self.reply(429, 'Noch beschaeftigt. Bitte erneut versuchen.')
        try:
            try:
                photo = self.rfile.read(length)
            except (socket.timeout, OSError):
                return self.reply(408, 'Bilduebertragung abgebrochen.')
            if len(photo) != length:
                return self.reply(400, 'Bild unvollstaendig.')
            if photo.startswith(b'\xff\xd8\xff'):
                mime = 'image/jpeg'
            elif photo.startswith(b'\x89PNG\r\n\x1a\n'):
                mime = 'image/png'
            else:
                return self.reply(415, 'Bitte JPEG oder PNG senden.')
            image_url = 'data:' + mime + ';base64,' + base64.b64encode(photo).decode('ascii')
            payload = {
                'model': MODEL, 'stream': False, 'max_tokens': 256,
                'chat_template_kwargs': {'enable_thinking': False},
                'messages': [{'role': 'user', 'content': [
                    {'type': 'text', 'text': 'Beschreibe das Bild auf Deutsch in hoechstens zwei kurzen Saetzen. Sage ausdruecklich, wenn etwas nicht eindeutig erkennbar ist.'},
                    {'type': 'image_url', 'image_url': {'url': image_url}},
                ]}],
            }
            headers = {'Content-Type': 'application/json'}
            if os.environ.get('OMLX_API_KEY'):
                headers['Authorization'] = 'Bearer ' + os.environ['OMLX_API_KEY']
            req = Request(UPSTREAM, data=json.dumps(payload).encode(), headers=headers)
            try:
                with OPENER.open(req, timeout=180) as response:
                    result = json.load(response)
                text = result['choices'][0]['message']['content']
                if not isinstance(text, str) or not text.strip():
                    raise ValueError('Keine Textantwort')
                self.reply(200, text.strip())
            except HTTPError as e:
                self.reply(502, 'oMLX meldet HTTP ' + str(e.code) + '. Modell und Anmeldung pruefen.')
            except (TimeoutError, socket.timeout):
                self.reply(504, 'oMLX hat nicht rechtzeitig geantwortet.')
            except (URLError, OSError):
                self.reply(502, 'oMLX nicht erreichbar. Mac und SSH-Tunnel pruefen.')
            except (ValueError, KeyError, IndexError, TypeError):
                self.reply(502, 'Keine verwendbare Textantwort von oMLX erhalten.')
        finally:
            BUSY.release()


if __name__ == '__main__':
    # Replace SERVER_TAILSCALE_IP with this Ubuntu server's Tailscale IP
    # or set the address through CAMERA_HOST.
    host = os.environ.get('CAMERA_HOST', 'SERVER_TAILSCALE_IP')
    port = int(os.environ.get('CAMERA_PORT', '8090'))
    print(f'Bilddienst: http://{host}:{port}', flush=True)
    print(f'Modell: {MODEL}; Zugriffsschluessel: {TOKEN_FILE}', flush=True)
    print('Nur im privaten Heimnetz verwenden; keine Router-Portfreigabe einrichten.', flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
