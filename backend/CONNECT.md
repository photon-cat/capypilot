# Using comma **connect** as the frontend

This backend implements the API contract the official open-source web app
[commaai/connect](https://github.com/commaai/connect) expects, so you can run
connect unmodified against capypilot — no fork, no code changes, just config.

## 1. Point connect at this backend

connect reads its service URLs from `public/config.js` (rendered from
`config.js.template` at deploy time). Set all three to your backend host:

```js
// connect/public/config.js
window.COMMA_URL_ROOT   = 'http://localhost:8000/';   // this API
window.ATHENA_URL_ROOT  = 'http://localhost:8001/';   // athena WebSocket/RPC
window.BILLING_URL_ROOT = 'http://localhost:8000/';   // unused (no prime); harmless
window.USERADMIN_URL_ROOT = 'http://localhost:8000/'; // unused
```

In production these must be the public **https** URLs of your reverse proxy. The
trailing slash matters (the API lib concatenates `baseUrl + endpoint`).

Then run connect:

```bash
git clone https://github.com/commaai/connect && cd connect
npm install
npm start          # dev server on http://localhost:3000
```

The backend already sends permissive CORS (`CORS_ORIGINS=*` by default); lock it
to your connect origin in production.

## 2. Log in

comma's real login goes through Google/Apple/GitHub OAuth, whose redirect URIs
are hardcoded to `api.comma.ai` — that part can't be self-hosted without forking
[my-comma-auth](https://github.com/commaai/my-comma-auth). Instead, mint a token
against the local admin user and hand it to connect.

Get a token (same call connect's login makes — `provider=email`, `code=email:password`):

```bash
curl -s -X POST http://localhost:8000/v2/auth/ \
  -d provider=email -d code='admin@local:admin'
# -> {"access_token":"eyJ..."}
```

Then, in the browser devtools console on the connect tab, store it the way
connect's auth lib expects and reload:

```js
localStorage.setItem('authorization', 'eyJ...');   // the access_token
location.reload();
```

connect treats "a token is present" as logged-in and will start calling the API
with `Authorization: JWT <token>`.

## 3. What works

| connect feature | endpoint(s) | status |
|---|---|---|
| Login / session | `POST /v2/auth/`, `GET /v1/me/` | ✅ (email/password token) |
| Device list & selection | `GET /v1/me/devices/`, `GET /v1.1/devices/{id}/` | ✅ |
| Online/offline indicator | `last_athena_ping` (driven by the Athena WS) | ✅ |
| Set device alias | `PATCH /v1/devices/{id}/` | ✅ |
| Device stats / location | `GET /v1.1/devices/{id}/stats`, `GET /v1/devices/{id}/location` | ✅ |
| Drive (route) list | `GET /v1/devices/{id}/routes_segments` | ✅ |
| Map path | `{route.url}/{i}/coords.json` | ✅ (from indexed GPS) |
| Video playback | `GET /v1/route/{fullname}/qcamera.m3u8` (HLS) | ✅ (needs qcamera uploaded) |
| File downloads | `GET /v1/route/{route}/files` | ✅ |
| **Prime (all features, ungated)** | device `prime:true` + `GET /v1/prime/subscription` | ✅ (free — no billing) |
| **Navigation** | `POST /v1/navigation/{id}/set_destination`, `/next`, `/locations` | ✅ (sends destinations to the car) |

## 4. Limitations / notes

- **OAuth providers** aren't self-hosted (see step 2). Token injection is the
  workaround; or fork my-comma-auth to point at your own OAuth apps + a
  `/v2/auth/{provider}/redirect/` you implement.
- **Prime is ungated**: every device is reported as a fully-subscribed prime
  device (`prime:true`, `prime_type:4`, `eligible_features.prime_data/nav`), and
  `/v1/prime/subscription` returns an active **$0.00** subscription. There is no
  billing — the stripe/cancel/subscribe_info endpoints are benign no-ops.
- **Navigation** is fully implemented: `set_destination` pushes to a live device
  over Athena (`setNavDestination`), or queues it as the device's `next`
  destination to pull on reconnect; favorites/recents persist via `locations`.
  (connect's web map itself is read-only — these endpoints serve the device and
  the comma mobile app.)
- **Timeline events** (`events.json`) are served empty — the log parser doesn't
  extract alert/engagement events yet, so the scrubber has no event markers and
  video starts at offset 0. Map path and playback are unaffected.
- **Video is HLS**: connect feeds `qcamera.m3u8` to hls.js, which does *not*
  send the auth header — the playlist is authorized by the signed `exp`/`sig`
  query params embedded in `routes_segments` (see `app/share.py`). The playlist
  references presigned MinIO URLs for each `qcamera.ts`; MinIO is configured with
  `MINIO_API_CORS_ALLOW_ORIGIN=*` so the browser can fetch them cross-origin.
- **Prime / billing / clips / navigation** are not implemented; connect degrades
  gracefully (the route browsing flow doesn't need them).
- **Maps/geocoding** in connect call Mapbox directly with connect's own token —
  unrelated to this backend.

## 5. Verify it

With the stack up (`docker compose up -d`):

```bash
docker compose exec api python scripts/provision_device.py make-qlog /tmp/q.zst
docker compose cp api:/tmp/q.zst ./q.zst
./.venv/bin/python scripts/verify_connect.py --qlog ./q.zst
# -> drives every connect endpoint and asserts the response shapes
#    ... CONNECT COMPATIBILITY VERIFIED
```
