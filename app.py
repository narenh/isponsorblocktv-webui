import asyncio
import json
import os
import socket
import logging
from pathlib import Path
from urllib.parse import quote
from flask import Flask, request, jsonify, send_from_directory
import aiohttp

app = Flask(__name__, static_folder="static")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DOCKER_SOCKET_PATH = Path(os.environ.get("DOCKER_SOCKET_PATH", "/var/run/docker.sock"))

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "/app/data/config.json"))
ISPONSORTV_CONTAINER = os.environ.get("ISPONSORTV_CONTAINER", "iSponsorBlockTV")

DEFAULT_CONFIG = {
    "devices": [],
    "apikey": "",
    "skip_categories": ["sponsor", "selfpromo", "music_offtopic"],
    "channel_whitelist": [],
    "skip_count_tracking": True,
    "mute_ads": False,
    "skip_ads": True,
    "minimum_skip_length": 1,
    "auto_play": False,
    "join_name": "iSponsorBlockTV",
    "use_proxy": False
}


def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return dict(DEFAULT_CONFIG)


def save_config(config):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


async def pair_with_youtube(tv_code: str):
    """Call YouTube's Lounge pairing API to exchange a TV code for a screen_id."""
    clean_code = tv_code.replace("-", "").replace(" ", "").strip()
    url = "https://www.youtube.com/api/lounge/pairing/get_screen"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (compatible; iSponsorBlockTV)",
        "Origin": "https://www.youtube.com",
    }
    data = {"pairing_code": clean_code}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, data=data) as resp:
            if resp.status != 200:
                raise ValueError(f"YouTube API returned HTTP {resp.status}. The code may be expired or invalid.")
            result = await resp.json(content_type=None)

    if "screen" not in result:
        raise ValueError(f"Unexpected response from YouTube: {result}")

    screen = result["screen"]
    screen_id = screen.get("screenId") or screen.get("screen_id")
    device_name = screen.get("name", "YouTube TV")

    if not screen_id:
        raise ValueError("No screen_id in YouTube response")

    return screen_id, device_name


def restart_container():
    """Restart the iSponsorBlockTV container via Docker Unix socket."""
    if not DOCKER_SOCKET_PATH.exists():
        logger.warning("docker socket unavailable; container restart skipped.")
        return False, "Docker socket unavailable; restart skipped. Restart the iSponsorBlockTV container manually."

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(str(DOCKER_SOCKET_PATH))
            container = quote(ISPONSORTV_CONTAINER, safe="")
            request = (
                f"POST /containers/{container}/restart?t=30 HTTP/1.1\r\n"
                "Host: localhost\r\n"
                "Content-Length: 0\r\n"
                "Connection: close\r\n"
                "\r\n"
            )
            sock.sendall(request.encode("utf-8"))

            raw = b""
            while b"\r\n\r\n" not in raw:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                raw += chunk

        response_line = raw.split(b"\r\n", 1)[0].decode("utf-8", errors="replace")
        parts = response_line.split(" ", 2)
        if len(parts) < 2:
            return False, f"Docker API returned invalid response: {response_line}"

        status_code = int(parts[1])
        if status_code in (200, 204):
            return True, "Restarted successfully"
        reason = parts[2] if len(parts) > 2 else ""
        return False, f"Docker API returned {status_code} {reason}".strip()
    except PermissionError as e:
        logger.warning(f"Permission error accessing Docker socket: {e}")
        return False, "Permission denied accessing Docker socket; restart skipped. Restart manually."
    except Exception as e:
        logger.warning(f"Docker socket restart failed: {e}")
        return False, f"Container restart failed: {e}"


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/devices", methods=["GET"])
def get_devices():
    config = load_config()
    return jsonify({"devices": config.get("devices", [])})


@app.route("/api/add-device", methods=["POST"])
def add_device():
    body = request.get_json(force=True)
    tv_code = (body.get("tv_code") or "").strip()
    custom_name = (body.get("name") or "").strip()

    if not tv_code:
        return jsonify({"success": False, "error": "TV code is required"}), 400

    # Pair with YouTube
    try:
        screen_id, yt_name = asyncio.run(pair_with_youtube(tv_code))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

    device_name = custom_name or yt_name or "YouTube TV"

    # Load config and check for duplicates
    config = load_config()
    devices = config.get("devices", [])

    for d in devices:
        if d.get("screen_id") == screen_id:
            return jsonify({"success": False, "error": f"Device '{d.get('name')}' is already paired"}), 400

    # Add device
    devices.append({"screen_id": screen_id, "name": device_name, "offset": 0})
    config["devices"] = devices
    save_config(config)

    # Restart container
    restarted, restart_msg = restart_container()

    return jsonify({
        "success": True,
        "device": {"screen_id": screen_id, "name": device_name},
        "restarted": restarted,
        "restart_message": restart_msg
    })


@app.route("/api/remove-device", methods=["POST"])
def remove_device():
    body = request.get_json(force=True)
    screen_id = (body.get("screen_id") or "").strip()
    if not screen_id:
        return jsonify({"success": False, "error": "screen_id required"}), 400

    config = load_config()
    before = len(config.get("devices", []))
    config["devices"] = [d for d in config.get("devices", []) if d.get("screen_id") != screen_id]

    if len(config["devices"]) == before:
        return jsonify({"success": False, "error": "Device not found"}), 404

    save_config(config)
    restarted, restart_msg = restart_container()

    return jsonify({"success": True, "restarted": restarted, "restart_message": restart_msg})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
