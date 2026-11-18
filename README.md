# Camera-Pi

Press a button to take a photo. A short description then appears on the Raspberry Pi's OLED display.

The Pi handles the camera, button, and display. A Python service on Ubuntu receives the image and forwards it to oMLX on a Mac. The model is asked to describe the image in German in no more than two short sentences and to mention anything it cannot identify clearly.

## How it works

```mermaid
flowchart LR
    Pi[Camera-Pi with button and OLED] -->|JPEG over Tailscale, port 8090| Ubuntu[Image service on Ubuntu]
    Ubuntu -->|Port 8000 through SSH tunnel| Mac[oMLX on Mac]
    Mac -->|Image description| Ubuntu
    Ubuntu -->|Text for the display| Pi
```

The Pi can use Wi-Fi or a phone hotspot. Both the Pi and Ubuntu need a working Tailscale connection. The Mac must be able to reach Ubuntu over SSH and stay available with oMLX and the SSH tunnel running during image analysis.

### Files

| File | Purpose |
| --- | --- |
| `camera_button.py` | Reads the button, takes a photo, sends it to Ubuntu, and displays the response. |
| `camera_bridge.py` | Checks the access token, receives the image, and calls the oMLX API. |
| `kamera.service` | Runs the Pi application as a systemd service. |
| `camera-pi/` | Contains separate Python files and service templates for the Pi and Ubuntu. |

## Hardware

The code is configured for a Raspberry Pi Zero 2 W with an OV5647 camera, a push button, and a 128 × 32 pixel SSD1306 I²C OLED display.

| Component | Pi connection |
| --- | --- |
| Camera | CSI camera connector with a suitable ribbon cable |
| Button | Between GPIO17, physical pin 11, and GND, such as pin 9 |
| OLED SDA | GPIO2, physical pin 3 |
| OLED SCL | GPIO3, physical pin 5 |
| OLED GND | GND, such as physical pin 6 |
| OLED VCC | 3.3 V, physical pin 1, for a module rated for this voltage |

The button uses the internal pull-up resistor. The display uses I²C bus 1 at address `0x3c`. Shut down the Pi and disconnect its power before wiring anything.

## Before you start

You need Raspberry Pi OS with `rpicam-still`, Ubuntu with Python 3 and SSH access, and a Mac running oMLX with a loaded vision model. Set up oMLX for your Mac first. Its OpenAI-compatible `/v1/chat/completions` endpoint must accept images as data URLs.

Replace all placeholders before setup. `PI_USER` is the Linux username on the Pi, and `SERVER_USER` is the Linux username on Ubuntu. Adjust the home directory paths if they are outside `/home`. `SERVER_TAILSCALE_IP` and `PI_TAILSCALE_IP` are the devices' Tailscale addresses. Run `tailscale ip -4` on each device to find them. Comments in the project files explain the values you need to supply.

Each step tells you which device to use. In an SSH session, check the username and hostname in the terminal prompt to confirm where you are working.

## Set up the Pi

Place `camera_button.py` and `kamera.service` in the Pi user's home directory. Install the packages on the Pi.

```bash
sudo apt update
sudo apt install python3-venv python3-gpiozero python3-lgpio python3-pil rpicam-apps i2c-tools fonts-dejavu-core
python3 -m venv --system-site-packages ~/kamera-env
~/kamera-env/bin/python -m pip install luma.oled
# Replace PI_USER with your Linux username on the Pi.
sudo usermod -aG i2c,gpio PI_USER
sudo raspi-config
```

Enable I²C under the interface options in `raspi-config`. Restart the Pi to apply the changes and the group memberships.

```bash
sudo reboot
```

After logging in again, check the display and camera.

```bash
i2cdetect -y 1
rpicam-still --list-cameras
timeout -k 3s 15s rpicam-still --nopreview --timeout 2000 --width 1024 --height 768 --output /tmp/kamera-test.jpg
```

With the display connected, `i2cdetect` should show address `3c`. A successful capture reports `Still capture image received` and creates `/tmp/kamera-test.jpg`.

The `--system-site-packages` option makes the GPIO packages installed through apt available in the virtual environment. See the [GPIO Zero documentation](https://gpiozero.readthedocs.io/en/stable/installing.html). Display wiring is covered in the [Luma.OLED documentation](https://luma-oled.readthedocs.io/en/stable/hardware.html).

## Start the Ubuntu image service

Place `camera_bridge.py` at `/home/SERVER_USER/camera_bridge.py` on Ubuntu. The service uses the Python standard library.

```bash
sudo apt update
sudo apt install python3
CAMERA_HOST=SERVER_TAILSCALE_IP python3 ~/camera_bridge.py
```

On its first start, the program creates `.camera-token` beside the script and fills it with a random access token. It reuses an existing file. Leave the terminal open for the initial test.

`CAMERA_HOST` must match the address the Pi uses to reach the service. The code contains the placeholder `SERVER_TAILSCALE_IP`. Set the environment variable to Ubuntu's Tailscale IP when starting the service, or replace the placeholder in the code.

### Copy the access token to the Pi

Open a second SSH session to Ubuntu. Copy the token from Ubuntu to the Pi.

```bash
# Replace SERVER_USER and PI_USER with the Linux usernames on each device.
# Replace PI_TAILSCALE_IP with the Pi's Tailscale IP.
scp /home/SERVER_USER/.camera-token PI_USER@PI_TAILSCALE_IP:/home/PI_USER/.camera-token
ssh PI_USER@PI_TAILSCALE_IP 'chmod 600 /home/PI_USER/.camera-token'
```

Both devices need the same token. The Pi reads it from the home directory of the user running the application. The bridge reads it from the directory containing its Python file.

## Mac and SSH tunnel

Start oMLX on the Mac with a vision model. The bridge defaults to the model name `Qwen3.5-9B-4bit`. This must match the name exposed by your oMLX API.

In the Mac terminal, check whether the API is reachable on the expected local port.

```bash
curl --noproxy '*' --connect-timeout 5 --max-time 10 http://127.0.0.1:8000/v1/models
```

If your API requires authentication, include the header `Authorization: Bearer YOUR_API_KEY` in this test. Set the same key as `OMLX_API_KEY` for the bridge.

Then open the tunnel from the Mac to Ubuntu.

```bash
ssh -N -T -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -R 127.0.0.1:8000:127.0.0.1:8000 SERVER_USER@SERVER_TAILSCALE_IP
```

Leave this terminal open. The tunnel forwards connections to `127.0.0.1:8000` on Ubuntu to `127.0.0.1:8000` on the Mac. Ubuntu's SSH server must allow remote port forwarding, and that port must be available on Ubuntu.

In a separate Ubuntu session, check the same URL with `curl`. A response from `/v1/models` confirms that the API is reachable. Use the camera button afterward to test image processing. The SSH options are described in the [OpenSSH manual](https://man.openbsd.org/ssh_config).

## Start the Pi application

On the Pi, open `~/kamera.service` and update the following line with Ubuntu's Tailscale IP.

```ini
# Replace SERVER_TAILSCALE_IP with the Ubuntu server's Tailscale IP.
Environment=CAMERA_BRIDGE_URL=http://SERVER_TAILSCALE_IP:8090/describe
```

Replace `PI_USER` in `User`, `WorkingDirectory`, and `ExecStart` with your Pi username and check the paths. Then install and enable the service on the Pi.

```bash
sudo cp ~/kamera.service /etc/systemd/system/kamera.service
sudo systemctl daemon-reload
sudo systemctl enable --now kamera.service
```

Check the service status and follow its logs.

```bash
systemctl status kamera.service --no-pager
journalctl -u kamera.service -f
```

After editing the installed service file, run `sudo systemctl daemon-reload` and `sudo systemctl restart kamera.service`.

## First end-to-end test

Run these commands on the Pi using Ubuntu's Tailscale IP.

```bash
tailscale ping --c=3 SERVER_TAILSCALE_IP
curl --noproxy '*' --connect-timeout 5 --max-time 8 http://SERVER_TAILSCALE_IP:8090/health
```

The health check should return `Bilddienst erreichbar (oMLX nicht mitgeprueft).`, meaning the image service is reachable and oMLX has not been checked. It tests the HTTP connection to the bridge. For a complete photo request, oMLX and the tunnel must also be running.

The display shows `Kamera bereit` (camera ready). Press the button once. During capture and analysis, it shows `Bitte warten` (please wait). Button presses during processing are discarded.

The display shows two lines of the response per page and advances every five seconds. Press the button to return to the start screen. Press it again to take another photo. The same controls apply after an error message.

## Start the Ubuntu service automatically

For regular use, create `/etc/systemd/system/camera-bridge.service` on Ubuntu with the following contents. Replace the IP and username placeholders and check the paths. A separate template is also available at `camera-pi/camera-bridge.service`.

```ini
[Unit]
Description=Camera-Pi image service
Wants=network-online.target tailscaled.service
After=network-online.target tailscaled.service
StartLimitIntervalSec=0

[Service]
Type=simple
# Replace SERVER_USER everywhere with your Linux username on Ubuntu.
User=SERVER_USER
WorkingDirectory=/home/SERVER_USER
Environment=PYTHONUNBUFFERED=1
# Replace SERVER_TAILSCALE_IP with the Ubuntu server's Tailscale IP.
Environment=CAMERA_HOST=SERVER_TAILSCALE_IP
ExecStart=/usr/bin/python3 /home/SERVER_USER/camera_bridge.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

If the bridge is still running manually, stop it with `Ctrl+C` to free the port. Then enable the service on Ubuntu.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now camera-bridge.service
sudo journalctl -u camera-bridge.service -n 30 --no-pager
```

If the Tailscale address is unavailable during boot, systemd retries the start after five seconds. Start oMLX and the SSH tunnel on the Mac separately.

## Configuration

Set these variables when starting the program manually, or add them as `Environment=` entries in the relevant service file.

| Program | Variable | Default in the code |
| --- | --- | --- |
| Pi | `CAMERA_BRIDGE_URL` | `http://SERVER_TAILSCALE_IP:8090/describe` (replace the placeholder) |
| Bridge | `CAMERA_HOST` | `SERVER_TAILSCALE_IP` (replace the placeholder) |
| Bridge | `CAMERA_PORT` | `8090` |
| Bridge | `CAMERA_MODEL` | `Qwen3.5-9B-4bit` |
| Bridge | `OMLX_URL` | `http://127.0.0.1:8000/v1/chat/completions` |
| Bridge | `OMLX_API_KEY` | Optional bearer token for the model API |

Capture size, button pin, display address, and page timing are set directly in `camera_button.py`. Photos are taken at 1024 × 768 pixels. The bridge accepts JPEG and PNG images up to 5 MiB and processes one request at a time. Capture has a 45-second timeout, the oMLX request has a 180-second timeout, and the Pi's HTTP request has a 200-second timeout.

## Use a hotspot

If the Pi uses NetworkManager and both connections are already saved, you can limit automatic connections to the hotspot. Run these commands on the Pi, replacing `HOTSPOT` and `WIFI` with the profile names shown by the first command.

```bash
nmcli connection show
sudo nmcli connection modify "HOTSPOT" connection.autoconnect yes
sudo nmcli connection modify "WIFI" connection.autoconnect no
sudo nmcli connection up "HOTSPOT"
```

Disable automatic connections for any other saved Wi-Fi profiles the Pi could reach. Switching networks can disconnect an SSH session using the previous Wi-Fi address. Once the new connection is established, you can reconnect over Tailscale. With these settings, the Pi has no automatic Wi-Fi fallback when the hotspot is unavailable.

To re-enable automatic connections for a Wi-Fi profile, run `sudo nmcli connection modify "WIFI" connection.autoconnect yes`. See the [NetworkManager documentation](https://networkmanager.dev/docs/api/latest/nm-settings-nmcli.html) for these settings.

## Troubleshooting

### Tailscale is connected, but the bridge does not respond

From the Pi, run `tailscale ping` and then check `/health`, as in the initial test. A connection through a DERP relay can work for this project.

For `Connection refused`, check on Ubuntu whether a process is listening on port 8090.

```bash
sudo ss -ltnp 'sport = :8090'
```

An empty result means no TCP service is listening on that port. Start the bridge and check its output. If it is bound to a home network address, restart it with `CAMERA_HOST` set to the Tailscale IP. For a timeout, also check the firewall and Tailscale access rules for TCP port 8090.

### Camera does not respond

The display message `Kamera antwortet nicht.` appears when `rpicam-still` is still running after 45 seconds. On the Pi, stop the button service before testing the camera separately.

```bash
sudo systemctl stop kamera.service
timeout -k 3s 15s rpicam-still --nopreview --timeout 2000 --width 1024 --height 768 --output /tmp/kamera-test.jpg > /tmp/kamera-test.log 2>&1
tail -n 25 /tmp/kamera-test.log
```

`Still capture image received` means the camera delivered an image. For repeated `Input/output error` or `Device timeout` messages, shut down the Pi with `sudo poweroff`. Once it has shut down, disconnect power and check the ribbon cable at both ends. Then repeat the test. If the error persists, inspect the kernel messages with `sudo journalctl -k -b --no-pager | tail -n 60`. Further checks are described in the [Raspberry Pi camera troubleshooting guide](https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/accessories/camera/troubleshooting.adoc).

After a successful test, restart the button service with `sudo systemctl start kamera.service`.

### Display messages

The application displays German messages. The table below explains what to check for each one.

| Message | What to check |
| --- | --- |
| `Einrichtung fehlt` | Check for a readable, nonempty `.camera-token` in the Pi user's home directory. |
| `rpicam-still fehlt.` | Check that `rpicam-apps` is installed on the Pi. |
| `Kameraaufnahme fehlgeschlagen.` | Read the camera output in the Pi service journal. |
| `Zugriffsschluessel pruefen.` | Check that the Pi and bridge use the same token. HTTP 401 indicates a missing or incorrect token. |
| `Keine Verbindung. WLAN, Tailscale und Ubuntu pruefen.` | Check the network, Tailscale, bridge address, and port. |
| `Mac, oMLX und Tunnel pruefen.` | Check oMLX, the tunnel, model name, and API key if required. The bridge returns HTTP 502. |
| `KI braucht zu lange. Erneut versuchen.` | The oMLX request timed out. Check the Mac and model workload. |
| `Bilddienst beschaeftigt. Erneut druecken.` | The bridge is already processing a request. Wait before trying again. |

The Pi journal includes the full error response from the bridge. Check it for details when the short display message is insufficient.

## Images and access tokens

The Pi saves each photo in a temporary directory, which is removed after the request finishes or a handled error occurs. The bridge processes images in memory. The oMLX server receives the image for analysis. Any logs or files written by oMLX depend on its configuration. The manual camera tests in this README save their photo at `/tmp/kamera-test.jpg`.

The bridge checks a bearer token for image requests. `/health` is accessible without a token. In this setup, HTTP traffic to the bridge travels over Tailscale, and the connection from Ubuntu to the Mac goes through the SSH tunnel. Bind the bridge to the intended Tailscale address and keep it behind the router without public port forwarding.

Keep `.camera-token` and API keys out of the GitHub repository. Before publishing, add entries like these to your `.gitignore`.

```gitignore
.camera-token
.env
.env.*
kamera-env/
__pycache__/
*.pyc
```

## Verification status

This guide has been checked against the included code. The Python files and shell examples have passed syntax checks. A fresh installation covering image analysis and automatic startup has yet to be tested on the devices using this guide.
