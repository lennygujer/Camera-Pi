import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler

from gpiozero import Button
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306
from PIL import Image, ImageDraw, ImageFont

# Replace SERVER_TAILSCALE_IP with the Ubuntu server's Tailscale IP
# or set CAMERA_BRIDGE_URL to the complete URL.
BRIDGE_URL = os.environ.get('CAMERA_BRIDGE_URL', 'http://SERVER_TAILSCALE_IP:8090/describe')
TOKEN_FILE = Path.home() / '.camera-token'
PAGE_SECONDS = 5


def split_lines(text, font, width=128):
    """Wrap using actual glyph widths, including unusually long words."""
    lines = []
    current = ''
    for word in text.split():
        candidate = (current + ' ' + word).strip()
        if font.getlength(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ''
        for letter in word:
            if current and font.getlength(current + letter) > width:
                lines.append(current)
                current = ''
            current += letter
    if current:
        lines.append(current)
    return lines or ['Keine Textantwort.']


def describe(token):
    camera = shutil.which('rpicam-still')
    if not camera:
        raise RuntimeError('rpicam-still fehlt.')

    with tempfile.TemporaryDirectory(prefix='kamera-') as folder:
        photo = Path(folder) / 'foto.jpg'
        try:
            capture = subprocess.run(
                [camera, '--nopreview', '--timeout', '2000', '--width', '1024',
                 '--height', '768', '--output', str(photo)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=45,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError('Kamera antwortet nicht.') from None
        if capture.returncode != 0 or not photo.exists():
            print(capture.stdout[-3000:], flush=True)
            raise RuntimeError('Kameraaufnahme fehlgeschlagen.')
        data = photo.read_bytes()
        if not data.startswith(b'\xff\xd8') or len(data) > 5 * 1024 * 1024:
            raise RuntimeError('Ungueltiges oder zu grosses Foto.')
        request = Request(BRIDGE_URL, data=data, headers={
            'Content-Type': 'image/jpeg', 'Authorization': 'Bearer ' + token,
        })
        try:
            with build_opener(ProxyHandler({})).open(request, timeout=200) as response:
                answer = response.read(65536).decode('utf-8').strip()
        except HTTPError as e:
            detail = e.read(4096).decode('utf-8', errors='replace').strip()
            print(f'Bilddienst HTTP {e.code}: {detail}', flush=True)
            messages = {
                401: 'Zugriffsschluessel pruefen.',
                429: 'Bilddienst beschaeftigt. Erneut druecken.',
                502: 'Mac, oMLX und Tunnel pruefen.',
                504: 'KI braucht zu lange. Erneut versuchen.',
            }
            raise RuntimeError(messages.get(e.code, f'Bilddienst Fehler {e.code}.')) from None
        except (TimeoutError, URLError, OSError):
            raise RuntimeError('Keine Verbindung. WLAN, Tailscale und Ubuntu pruefen.') from None
        if not answer:
            raise RuntimeError('Die KI hat keinen Text geliefert.')
        return answer


def main():
    display = ssd1306(i2c(port=1, address=0x3c), width=128, height=32)
    try:
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 10)
    except OSError:
        font = ImageFont.load_default()

    def show(title, lines):
        image = Image.new('1', display.size)
        draw = ImageDraw.Draw(image)
        draw.text((0, -1), title, font=font, fill=255)
        for y, text in zip((10, 21), lines):
            draw.text((0, y), text, font=font, fill=255)
        display.display(image)

    button = None
    try:
        try:
            token = TOKEN_FILE.read_text().strip()
            if not token:
                raise ValueError('Leerer Schluessel')
        except (OSError, ValueError):
            show('Einrichtung fehlt', ['.camera-token', 'pruefen!'])
            raise RuntimeError(f'Zugriffsschluessel fehlt: {TOKEN_FILE}') from None

        pressed = threading.Event()
        result = queue.Queue()
        button = Button(17, pull_up=True, bounce_time=0.15)
        button.when_pressed = pressed.set
        show('Kamera bereit', ['Taster druecken', 'fuer ein Foto'])
        print('Bereit. Taster: Foto aufnehmen; nach Antwort: zurueck zum Start. Beenden: Ctrl+C.', flush=True)
        busy = False
        pages = []
        page_index = 0
        next_page = 0

        def work():
            try:
                result.put(('Antwort', describe(token)))
            except Exception as e:
                print(f'Fehler: {e}', flush=True)
                result.put(('Fehler', str(e)))

        while True:
            if pressed.is_set():
                pressed.clear()
                if not busy:
                    if pages:
                        pages = []
                        page_index = 0
                        show('Kamera bereit', ['Taster druecken', 'fuer ein Foto'])
                        print('Zurueck zum Start.', flush=True)
                    else:
                        busy = True
                        show('Bitte warten', ['Foto + KI-Analyse', 'kann etwas dauern'])
                        print('Neue Aufnahme und Bildanalyse ...', flush=True)
                        threading.Thread(target=work, daemon=True).start()

            try:
                title, text = result.get_nowait()
            except queue.Empty:
                pass
            else:
                busy = False
                pressed.clear()
                print(f'{title}: {text}', flush=True)
                lines = split_lines(text, font)
                pages = [lines[i:i+2] for i in range(0, len(lines), 2)]
                page_index = 0
                show(f'{title} 1/{len(pages)}', pages[0])
                next_page = time.monotonic() + PAGE_SECONDS

            if pages and len(pages) > 1 and time.monotonic() >= next_page:
                page_index = (page_index + 1) % len(pages)
                show(f'{title} {page_index+1}/{len(pages)}', pages[page_index])
                next_page = time.monotonic() + PAGE_SECONDS
            time.sleep(0.05)
    except KeyboardInterrupt:
        print('\nBeendet.', flush=True)
    finally:
        if button is not None:
            button.close()
        display.cleanup()


if __name__ == '__main__':
    main()
