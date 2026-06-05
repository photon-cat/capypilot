#!/usr/bin/env python3
"""Verify the commaai/connect compatibility layer end to end.

Registers a synthetic device, uploads a qlog (+ a stub qcamera segment so the
HLS endpoint has something to serve), then drives every endpoint the connect web
app depends on for its core flows and asserts the response shapes match what
`@commaai/api` consumers expect.

Run against a live stack (see README "Quick start"):

  docker compose exec api python scripts/provision_device.py make-qlog /tmp/q.zst
  docker compose cp api:/tmp/q.zst ./q.zst
  ./.venv/bin/python scripts/verify_connect.py --qlog ./q.zst
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import jwt
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.auth import derive_dongle_id  # noqa: E402
from scripts.provision_device import LOG_ID, SEGMENT, device_token, gen_keypair  # noqa: E402

OK = "✓"


def _put(url: str, headers: dict, data: bytes) -> None:
  r = requests.put(url, data=data, headers=headers, timeout=30)
  assert r.status_code in (200, 201), f"PUT failed {r.status_code}: {r.text}"


def run(api_host: str, qlog_path: str) -> int:
  priv, pub = gen_keypair()
  dongle_id = derive_dongle_id(pub)
  route = f"{dongle_id}|{LOG_ID}"
  print(f"dongle_id={dongle_id} route={route}")

  # ── register
  register_token = jwt.encode(
    {"register": True, "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, priv, algorithm="RS256"
  )
  r = requests.post(
    f"{api_host}/v2/pilotauth/",
    params={"public_key": pub, "register_token": register_token, "serial": "CAPY-VERIFY"},
    timeout=15,
  )
  r.raise_for_status()
  assert r.json()["dongle_id"] == dongle_id
  print(f"register {OK}")

  token = device_token(priv, dongle_id)
  auth = {"Authorization": "JWT " + token}

  # ── upload qlog + a stub qcamera.ts (so qcamera.m3u8 has a segment)
  with open(qlog_path, "rb") as f:
    qlog = f.read()
  for path, blob in ((f"{LOG_ID}--{SEGMENT}/qlog.zst", qlog),
                     (f"{LOG_ID}--{SEGMENT}/qcamera.ts", b"\x47" + b"\x00" * 187)):  # 1 TS packet
    u = requests.get(f"{api_host}/v1.4/{dongle_id}/upload_url/", params={"path": path}, headers=auth, timeout=15)
    u.raise_for_status()
    _put(u.json()["url"], u.json().get("headers", {}), blob)
  print(f"upload qlog + qcamera {OK}")

  # ── wait for ingest (MinIO webhook -> worker)
  s = requests.Session()
  tok = s.post(
    f"{api_host}/v2/auth/",
    data={"provider": "email", "code": f"{os.environ.get('ADMIN_EMAIL', 'admin@local')}:{os.environ.get('ADMIN_PASSWORD', 'admin')}"},
    timeout=15,
  )
  tok.raise_for_status()
  access_token = tok.json()["access_token"]
  assert access_token, "no access_token from /v2/auth/"
  print(f"/v2/auth/ -> access_token {OK}")
  s.headers["Authorization"] = "JWT " + access_token  # connect's header scheme

  rs = None
  for _ in range(30):
    resp = s.get(f"{api_host}/v1/devices/{dongle_id}/routes_segments", params={"route_str": route}, timeout=15)
    if resp.status_code == 200 and resp.json():
      rs = resp.json()[0]
      if rs.get("segment_numbers"):
        break
    time.sleep(1)
  assert rs, "route not indexed in time"

  # ── /v1/me/
  me = s.get(f"{api_host}/v1/me/", timeout=15).json()
  assert me["superuser"] is True and me["email"], me
  print(f"/v1/me/ {OK} (email={me['email']})")

  # ── /v1/me/devices/
  devs = s.get(f"{api_host}/v1/me/devices/", timeout=15).json()
  dev = next(d for d in devs if d["dongle_id"] == dongle_id)
  for k in ("device_type", "is_owner", "prime", "shared", "last_athena_ping", "openpilot_version"):
    assert k in dev, f"device missing {k}"
  print(f"/v1/me/devices/ {OK} ({len(devs)} device(s), type={dev['device_type']})")

  # ── /v1.1/devices/{dongle}/
  d1 = s.get(f"{api_host}/v1.1/devices/{dongle_id}/", timeout=15).json()
  assert d1["dongle_id"] == dongle_id
  print(f"/v1.1/devices/{{id}}/ {OK}")

  # ── routes_segments shape
  required = ["fullname", "url", "segment_start_times", "segment_end_times", "segment_numbers",
              "start_time_utc_millis", "end_time_utc_millis", "create_time", "distance",
              "maxqlog", "start_lat", "start_lng", "end_lat", "end_lng", "share_exp", "share_sig"]
  missing = [k for k in required if k not in rs]
  assert not missing, f"routes_segments missing {missing}"
  assert rs["fullname"] == route
  assert len(rs["segment_start_times"]) == len(rs["segment_numbers"])
  print(f"routes_segments {OK} (segments={rs['segment_numbers']}, dist={rs['distance']:.3f}mi, "
        f"start=({rs['start_lat']:.4f},{rs['start_lng']:.4f}))")

  # ── coords.json (open, fetched by connect without auth)
  coords = requests.get(f"{rs['url']}/{SEGMENT}/coords.json", timeout=15).json()
  assert coords and all({"t", "lat", "lng"} <= set(p) for p in coords), "bad coords.json"
  print(f"coords.json {OK} ({len(coords)} points)")

  # ── events.json (open, empty list tolerated)
  ev = requests.get(f"{rs['url']}/{SEGMENT}/events.json", timeout=15)
  assert ev.status_code == 200 and isinstance(ev.json(), list)
  print(f"events.json {OK}")

  # ── qcamera.m3u8 (authorized by signed exp/sig, not the JWT header)
  m3u8 = requests.get(
    f"{api_host}/v1/route/{route}/qcamera.m3u8",
    params={"exp": rs["share_exp"], "sig": rs["share_sig"]}, timeout=15,
  )
  assert m3u8.status_code == 200, f"m3u8 {m3u8.status_code}: {m3u8.text}"
  assert m3u8.text.startswith("#EXTM3U") and "#EXT-X-ENDLIST" in m3u8.text, m3u8.text
  print(f"qcamera.m3u8 {OK} (signed)")

  # ── m3u8 rejects a bad signature
  bad = requests.get(f"{api_host}/v1/route/{route}/qcamera.m3u8",
                     params={"exp": rs["share_exp"], "sig": "deadbeef"}, timeout=15)
  assert bad.status_code == 403, f"expected 403 for bad sig, got {bad.status_code}"
  print(f"qcamera.m3u8 bad-sig -> 403 {OK}")

  # ── prime (ungated): device reports prime + active subscription
  assert dev["prime"] is True and dev["eligible_features"]["nav"] is True, dev
  print(f"device prime={dev['prime']} nav={dev['eligible_features']['nav']} {OK}")
  sub = s.get(f"{api_host}/v1/prime/subscription", params={"dongle_id": dongle_id}, timeout=15).json()
  assert sub.get("user_id") and sub["amount"] == 0, sub
  print(f"/v1/prime/subscription {OK} (active, ${sub['amount'] / 100:.2f}, plan={sub['plan']})")

  # ── navigation: set a destination (device offline -> queued as "next")
  dest = {"latitude": 32.7157, "longitude": -117.1611, "place_name": "1441 State St",
          "place_details": "San Diego, CA 92101"}
  sd = s.post(f"{api_host}/v1/navigation/{dongle_id}/set_destination", json=dest, timeout=15).json()
  assert sd["success"] and sd["saved_next"] is True, sd
  print(f"set_destination {OK} (queued: saved_next={sd['saved_next']})")

  # device pulls its queued destination (device JWT, not the user token)
  nxt = requests.get(f"{api_host}/v1/navigation/{dongle_id}/next", headers=auth, timeout=15).json()
  assert nxt and nxt["place_name"] == dest["place_name"], nxt
  print(f"navigation/next {OK} (got '{nxt['place_name']}')")
  # next is one-shot: a second pull is empty
  nxt2 = requests.get(f"{api_host}/v1/navigation/{dongle_id}/next", headers=auth, timeout=15).json()
  assert nxt2 is None, f"expected null after consume, got {nxt2}"
  print(f"navigation/next consumed -> null {OK}")

  # saved locations: a "recent" was recorded by set_destination; add a favorite
  put = s.put(f"{api_host}/v1/navigation/{dongle_id}/locations",
              json={"latitude": 37.4, "longitude": -122.1, "place_name": "Home",
                    "place_details": "", "save_type": "favorite", "label": "Home"}, timeout=15).json()
  assert put["success"], put
  locs = s.get(f"{api_host}/v1/navigation/{dongle_id}/locations", timeout=15).json()
  types = {loc["save_type"] for loc in locs}
  assert "favorite" in types and "recent" in types, types
  print(f"navigation/locations {OK} ({len(locs)} saved: {sorted(types)})")

  print("\nCONNECT COMPATIBILITY VERIFIED (incl. prime + navigation)")
  return 0


def main() -> int:
  p = argparse.ArgumentParser()
  p.add_argument("--api", default=os.environ.get("API_HOST", "http://localhost:8000"))
  p.add_argument("--qlog", required=True)
  args = p.parse_args()
  return run(args.api, args.qlog)


if __name__ == "__main__":
  raise SystemExit(main())
