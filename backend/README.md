# capypilot backend

A self-hosted replacement for the comma.ai data backend. It does everything the
comma backend does with your driving data: registers devices, hands out upload
URLs, stores every artifact a drive produces (qlog, rlog, qcamera, fcamera,
ecamera, dcamera), accepts the Athena WebSocket control channel, parses & indexes
the logs, and serves a connect-style web UI to browse routes, see the GPS trace
on a map, and play back video.

It also speaks the **commaai/connect** API, so the official open-source
[connect](https://github.com/commaai/connect) web app can be used as the
frontend unmodified — just point its `config.js` at this backend. See
**[CONNECT.md](CONNECT.md)**.

Point a device at it by setting two env vars — **no firmware changes needed**:

```bash
export API_HOST=https://<your-host>      # REST: registration, upload URLs, route files
export ATHENA_HOST=wss://<your-host>     # WebSocket control channel
```

These are the only two endpoints the device uses
(`common/api.py`, `system/athena/athenad.py`).

## Architecture

| Service    | Role                                                                    |
|------------|-------------------------------------------------------------------------|
| `api`      | FastAPI REST (`/v2/pilotauth/`, `/v1.4/{id}/upload_url/`, `/v1/route/*`), web UI, ingest worker |
| `athena`   | WebSocket server `/ws/v2/{dongle_id}` — JSON-RPC control channel         |
| `postgres` | Devices, routes, segments, GPS trace, upload audit, stats               |
| `minio`    | S3-compatible blob storage for all uploaded files                       |

Auth is JWT signed by the device's own private key
(`/persist/comma/id_{rsa,ecdsa}`); we verify against the public key captured at
registration, so no shared secret with comma is needed. Large files never pass
through the API: the device PUTs directly to a presigned MinIO URL, and the
browser GETs playback directly from one.

### Storage key layout (tooling-compatible)

Files are stored at `{dongle_id}/{log_id}/{segment}/{filename}`, which is exactly
what `tools/lib/route.py` expects (`path.rsplit('/', 4)`), so openpilot's own
`Route` / `LogReader` work unmodified against this backend.

## Quick start

```bash
cd backend
cp .env.example .env          # adjust secrets/ports for production
docker compose up -d --build  # api:8000, athena:8001, minio:9100/9101
open http://localhost:8000    # web UI — log in as admin@local / admin
```

The API runs Alembic migrations and seeds the admin user on boot.

## Smoke test (no hardware required)

`scripts/provision_device.py` generates a keypair, registers, builds a synthetic
qlog, uploads it, and verifies it gets indexed end to end:

```bash
# build a synthetic qlog inside the container (has pycapnp + schema)
docker compose exec api python scripts/provision_device.py make-qlog /tmp/q.zst
docker compose cp api:/tmp/q.zst ./q.zst

# register + upload + verify from the host
./.venv/bin/python scripts/provision_device.py run --qlog ./q.zst
# -> registered ✓ / upload_url ✓ / uploaded ✓ / indexed ✓  SMOKE TEST PASSED
```

### Full device simulation

`scripts/simulate_device.py` simulates a complete comma device: it registers,
holds an Athena WebSocket open, uploads a multi-segment drive, answers relayed
remote commands (snapshot/network/reboot/listDataDirectory), performs a
backend-requested `uploadFilesToUrls`, receives a live `setNavDestination` push,
and sends stats/logs — then asserts the backend indexed and relayed everything.

```bash
docker compose exec api python scripts/provision_device.py make-qlog /tmp/q.zst
docker compose cp api:/tmp/q.zst ./q.zst
./.venv/bin/python scripts/simulate_device.py --qlog ./q.zst --segments 3
# -> ... SIMULATED DEVICE SCENARIO PASSED
```

There are also focused checks: `scripts/verify_connect.py` (connect API +
prime + nav) and `scripts/verify_device_control.py` (Athena JSON-RPC relay).

## Endpoints

Device-facing (comma-compatible):

| Method | Path                                  | Purpose                              |
|--------|---------------------------------------|--------------------------------------|
| POST   | `/v2/pilotauth/`                      | Register, returns `{dongle_id}`      |
| GET    | `/v1.4/{dongle_id}/upload_url/?path=` | Presigned PUT (`412` if already held)|
| GET    | `/v1/route/{route}/files`             | Presigned GET URLs, grouped          |
| GET    | `/v1/route/{route}`, `/v1/me`, `/v1/devices` | Metadata                      |
| WS     | `/ws/v2/{dongle_id}` (athena:8001)    | JSON-RPC control channel             |

commaai/connect-facing (so the official connect web app works as the frontend):

| Method | Path                                            | Purpose                          |
|--------|-------------------------------------------------|----------------------------------|
| POST   | `/v2/auth/`                                      | Token exchange → `{access_token}`|
| GET    | `/v1/me/`, `/v1/me/devices/`                     | Profile, device list             |
| GET    | `/v1.1/devices/{id}/`, `/stats`, `/location`     | Device detail / stats / location |
| GET    | `/v1/devices/{id}/routes_segments`              | Drive list (connect's main feed) |
| GET    | `/v1/route/{fullname}/qcamera.m3u8?exp=&sig=`   | HLS video (signed)               |
| GET    | `/connect/files/{id}/{log}/{seg}/coords.json`   | Per-segment GPS path for the map |
| GET    | `/v1/prime/subscription?dongle_id=`             | Active, free prime (ungated)     |
| POST   | `/v1/navigation/{id}/set_destination`           | Send a destination to the car    |
| GET/PUT| `/v1/navigation/{id}/next`, `/locations`        | Queued dest + saved favorites    |
| POST   | `{ATHENA_HOST}/{dongle_id}` (athena:8001)       | JSON-RPC device control (relayed)|

Live device control (snapshot, network info, reboot, …) is relayed to the
connected device over its Athena WebSocket; see [CONNECT.md](CONNECT.md).

Web UI JSON API is under `/api/*`; storage notifications hit `/internal/s3-event`.
Full connect setup is in **[CONNECT.md](CONNECT.md)**.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # pycapnp needs the capnp compiler;
                                            # if it won't build locally, omit it —
                                            # only the ingest worker uses it.
.venv/bin/python -m pytest tests/           # 37 tests; parser test runs where pycapnp is present
```

The Cap'n Proto schema is vendored under `schema/` so neither the openpilot
package nor the opendbc submodule is required to build or parse.

To verify the connect compatibility layer end to end against a running stack,
see `scripts/verify_connect.py` (documented in [CONNECT.md](CONNECT.md)).

## Production notes

- **TLS is required** — the device only talks to `https`/`wss`. Put `api` and
  `athena` behind a TLS-terminating reverse proxy (Caddy/nginx/Traefik) and set
  `S3_PUBLIC_ENDPOINT_URL` to the public MinIO URL.
- Change `USER_JWT_SECRET`, `ADMIN_PASSWORD`, and the MinIO/Postgres credentials.
- Set `CORS_ORIGINS` to your connect origin (e.g. `https://connect.example.com`)
  instead of the default `*` once you know where connect is served from.
- `dongle_id` is derived deterministically from the device public key
  (`sha256(pubkey)[:16]`) — idempotent registration, no central allocator. Swap
  in random+unique allocation if you prefer.
- The ingest worker runs in-process via the MinIO webhook. For high volume, move
  it to a real queue (Celery/RQ/arq); the worker boundary is already isolated.
- The Athena registry is in-memory (single replica). For multiple replicas, back
  it with Redis pub/sub.
