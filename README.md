# iSponsorBlockTV Web UI

A small Flask web UI for pairing and unpairing YouTube TV devices in
[iSponsorBlockTV](https://github.com/dmunozv04/iSponsorBlockTV) without running
the interactive `--setup` TUI.

It edits the **same `config.json`** that iSponsorBlockTV uses, then restarts the
iSponsorBlockTV container so the change takes effect.

## What it does

- Enter the TV code shown on your TV (YouTube → Settings → Link with TV code).
- The UI exchanges that code for a `screen_id` via YouTube's Lounge pairing API.
- The device is appended to the `devices` list in `config.json`.
- The iSponsorBlockTV container is restarted via the Docker socket.
- Existing paired devices are listed and can be removed.

Every other setting (API key, skip categories, channel whitelist, …) is left
untouched — keep using `--setup` or edit `config.json` directly for those.

## Requirements

- An existing iSponsorBlockTV deployment with a data directory containing
  `config.json` (created by `--setup`, or on first write by this UI).
- Docker and Docker Compose, with both containers on the **same Docker host**.
- Outbound HTTPS to `www.youtube.com` from the web UI container.

## Deployment (shared config directory)

The whole design assumes one host directory shared by both containers. Upstream
iSponsorBlockTV mounts its data directory at `/app/data` and reads
`/app/data/config.json`; this UI defaults to exactly the same path, so mount the
same host directory into both containers.

```
/PATH_TO_YOUR_DATA_DIR/          <- on the host
└── config.json

iSponsorBlockTV        : /PATH_TO_YOUR_DATA_DIR -> /app/data
isponsorblocktv-webui  : /PATH_TO_YOUR_DATA_DIR -> /app/data
```

### Docker Compose (recommended)

A ready-to-edit [`docker-compose.yml`](docker-compose.yml) is included. Replace
both occurrences of `/PATH_TO_YOUR_DATA_DIR` with your data directory, then:

```bash
docker compose up -d --build
```

Open `http://<host>:5000`.

```yaml
services:
  iSponsorBlockTV:
    image: ghcr.io/dmunozv04/isponsorblocktv
    container_name: iSponsorBlockTV
    restart: unless-stopped
    network_mode: host
    volumes:
      - /PATH_TO_YOUR_DATA_DIR:/app/data

  isponsorblocktv-webui:
    build: .
    container_name: isponsorblocktv-webui
    restart: unless-stopped
    ports:
      - "5000:5000"
    volumes:
      - /PATH_TO_YOUR_DATA_DIR:/app/data
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      CONFIG_PATH: /app/data/config.json
      ISPONSORTV_CONTAINER: iSponsorBlockTV
```

### Adding the UI to an existing iSponsorBlockTV setup

If iSponsorBlockTV already runs (Compose, `docker run`, Unraid, …), don't move
it — just point the UI at the same data directory and use the same container
name:

```bash
# from a clone of this repo
docker build -t isponsorblocktv-webui .

docker run -d \
  --name isponsorblocktv-webui \
  --restart=unless-stopped \
  -p 5000:5000 \
  -v /PATH_TO_YOUR_DATA_DIR:/app/data \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e ISPONSORTV_CONTAINER=iSponsorBlockTV \
  isponsorblocktv-webui
```

`ISPONSORTV_CONTAINER` must match the *actual* container name (`docker ps
--format '{{.Names}}'`). Under Compose, that is `container_name:` if set,
otherwise the generated `<project>-<service>-1` name.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `CONFIG_PATH` | `/app/data/config.json` | Path to the shared iSponsorBlockTV config file. |
| `ISPONSORTV_CONTAINER` | `iSponsorBlockTV` | Name (or ID) of the container to restart after a change. |
| `DOCKER_SOCKET_PATH` | `/var/run/docker.sock` | Docker socket used for the restart. Point it at e.g. `/var/run/user/1000/podman/podman.sock` for rootless Podman. |

The server listens on `0.0.0.0:5000`; the port is not configurable via
environment variable, so remap it with `-p 8080:5000` if needed.

If `config.json` does not exist yet, the first save creates it with
iSponsorBlockTV's defaults and an **empty `apikey`**. Run upstream `--setup` (or
add the key by hand) if you want API-key features.

## Restarting without the Docker socket

Mounting `/var/run/docker.sock` gives the container full control of the Docker
daemon, which is effectively root on the host. If you'd rather not:

- Omit the socket mount. Pairing still works and the config is still written —
  the UI reports `Docker socket unavailable; restart skipped` and you restart
  iSponsorBlockTV yourself (`docker restart iSponsorBlockTV`).
- Or restrict access with a filtering proxy. Note that the UI talks to a **Unix
  socket only** (no TCP support), so a TCP-based proxy such as
  `tecnativa/docker-socket-proxy` (`POST=1 CONTAINERS=1`) needs a Unix-socket
  front end, e.g. a sidecar running
  `socat UNIX-LISTEN:/shared/docker.sock,fork,mode=660 TCP:docker-socket-proxy:2375`,
  with `/shared` mounted into this container and
  `DOCKER_SOCKET_PATH=/shared/docker.sock`.

A `Permission denied accessing Docker socket` message means the container user
can't access the socket — on SELinux hosts try `-v /var/run/docker.sock:/var/run/docker.sock:z`.

## Security

There is **no authentication, no HTTPS and no CSRF protection**. Anyone who can
reach port 5000 can pair/unpair devices and, when the socket is mounted,
trigger a container restart. Bind it to a trusted network or put it behind an
authenticating reverse proxy; do not expose it to the internet.

## Local development

```bash
pip install flask aiohttp
CONFIG_PATH=./data/config.json python app.py
```

Then open `http://localhost:5000`. Without a Docker socket, restarts are
skipped and reported in the UI response.

## HTTP API

| Method | Endpoint | Body | Notes |
| --- | --- | --- | --- |
| `GET` | `/api/devices` | – | `{"devices": [...]}` from the config. |
| `POST` | `/api/add-device` | `{"tv_code": "...", "name": "optional"}` | Pairs with YouTube, saves, restarts. |
| `POST` | `/api/remove-device` | `{"screen_id": "..."}` | Removes the device, saves, restarts. |

Write responses include `restarted` (bool) and `restart_message` so a failed
restart is visible rather than silent.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `YouTube API returned HTTP 4xx. The code may be expired or invalid.` | TV codes are short-lived — reopen the TV's link screen for a fresh code. |
| `Device '<name>' is already paired` | That `screen_id` is already in `config.json`. |
| `Docker API returned 404` | `ISPONSORTV_CONTAINER` doesn't match a running container name. |
| Devices added but iSponsorBlockTV ignores them | The two containers aren't sharing one data directory — check both mounts resolve to the same host path. |

## Credits

- [dmunozv04/iSponsorBlockTV](https://github.com/dmunozv04/iSponsorBlockTV) — the
  service this UI configures.
- [SponsorBlock](https://sponsor.ajay.app/) — the segment data behind it.
