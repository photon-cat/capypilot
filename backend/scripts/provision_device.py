#!/usr/bin/env python3
"""Provision a fake device and exercise the full ingestion path — a smoke test
that needs no real hardware.

Two sub-commands:

  make-qlog <out.zst>         Build a synthetic qlog (needs pycapnp + schema).
                              Run inside the api container.

  run --qlog <file> [--api ..] [--s3 ..]
                              Generate a keypair, register, upload the qlog to
                              the presigned URL, then verify the route + GPS
                              trace got indexed. Run from the host.

Example end-to-end:
  docker compose exec api python scripts/provision_device.py make-qlog /tmp/q.zst
  docker compose cp api:/tmp/q.zst ./q.zst
  ./.venv/bin/python scripts/provision_device.py run --qlog ./q.zst
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import jwt
import requests
import zstandard as zstd
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.auth import derive_dongle_id  # noqa: E402

LOG_ID = "0000007b--abcdef1234"
SEGMENT = 0


# ── synthetic qlog ────────────────────────────────────────────────────────────
def make_qlog(out_path: str) -> None:
  import capnp

  capnp.remove_import_hook()
  cereal_path = os.environ.get("CEREAL_PATH", "/app/cereal")
  log = capnp.load(os.path.join(cereal_path, "log.capnp"))

  blobs: list[bytes] = []

  init = log.Event.new_message()
  init.logMonoTime = 0
  idat = init.init("initData")
  idat.dongleId = derive_dongle_id("synthetic")
  idat.version = "0.9.9-capy"
  idat.gitCommit = "deadbeefcafebabe0000000000000000"
  idat.wallTimeNanos = int(time.time() * 1e9)
  blobs.append(init.to_bytes())

  # a short GPS track near SF
  base_ms = int(time.time() * 1000)
  lat, lon = 37.7749, -122.4194
  for i in range(120):
    e = log.Event.new_message()
    e.logMonoTime = i * 500_000_000
    g = e.init("gpsLocationExternal")
    g.latitude = lat + i * 0.0002
    g.longitude = lon + i * 0.0001
    g.altitude = 10.0
    g.speed = 12.5
    g.bearingDeg = 45.0
    g.unixTimestampMillis = base_ms + i * 500
    g.hasFix = True
    g.flags = 1
    blobs.append(e.to_bytes())

  raw = b"".join(blobs)
  with open(out_path, "wb") as f:
    f.write(zstd.compress(raw, 10))
  print(f"wrote synthetic qlog: {out_path} ({os.path.getsize(out_path)} bytes, {len(blobs)} events)")


# ── full run ──────────────────────────────────────────────────────────────────
def gen_keypair() -> tuple[str, str]:
  key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
  priv = key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
  ).decode()
  pub = key.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
  ).decode()
  return priv, pub


def device_token(priv: str, dongle_id: str) -> str:
  now = datetime.now(timezone.utc)
  return jwt.encode(
    {"identity": dongle_id, "nbf": now, "iat": now, "exp": now + timedelta(hours=1)},
    priv, algorithm="RS256",
  )


def run(api_host: str, qlog_path: str) -> int:
  priv, pub = gen_keypair()
  dongle_id = derive_dongle_id(pub)
  print(f"derived dongle_id: {dongle_id}")

  # 1) register
  register_token = jwt.encode(
    {"register": True, "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, priv, algorithm="RS256"
  )
  r = requests.post(
    f"{api_host}/v2/pilotauth/",
    params={"public_key": pub, "register_token": register_token, "imei": "1", "imei2": "2", "serial": "CAPY01"},
    timeout=15,
  )
  r.raise_for_status()
  assert r.json()["dongle_id"] == dongle_id, r.text
  print("registered ✓")

  # 2) request an upload URL
  token = device_token(priv, dongle_id)
  path = f"{LOG_ID}--{SEGMENT}/qlog.zst"
  r = requests.get(
    f"{api_host}/v1.4/{dongle_id}/upload_url/",
    params={"path": path},
    headers={"Authorization": "JWT " + token},
    timeout=15,
  )
  r.raise_for_status()
  upload = r.json()
  print(f"got upload_url ✓ -> {upload['url'].split('?')[0]}")

  # 3) PUT the qlog to storage
  with open(qlog_path, "rb") as f:
    data = f.read()
  put = requests.put(upload["url"], data=data, headers=upload.get("headers", {}), timeout=30)
  assert put.status_code in (200, 201), f"{put.status_code} {put.text}"
  print(f"uploaded qlog ✓ ({len(data)} bytes)")

  # 4) verify indexing (ingest is driven by the MinIO webhook; poll briefly)
  sess = requests.Session()
  login = sess.post(f"{api_host}/api/login", json={"email": os.environ.get("ADMIN_EMAIL", "admin@local"),
                                                   "password": os.environ.get("ADMIN_PASSWORD", "admin")}, timeout=15)
  login.raise_for_status()
  route = f"{dongle_id}|{LOG_ID}"
  for _ in range(30):
    track = sess.get(f"{api_host}/api/routes/{route}/track", timeout=15)
    if track.status_code == 200 and track.json().get("points"):
      pts = track.json()["points"]
      files = sess.get(f"{api_host}/v1/route/{route}/files", timeout=15).json()
      print(f"indexed ✓ route={route} gps_points={len(pts)} qlogs={len(files.get('qlogs', []))}")
      print("SMOKE TEST PASSED")
      return 0
    time.sleep(1)
  print("FAILED: route not indexed in time", file=sys.stderr)
  return 1


def main() -> int:
  p = argparse.ArgumentParser()
  sub = p.add_subparsers(dest="cmd", required=True)
  mq = sub.add_parser("make-qlog")
  mq.add_argument("out")
  rn = sub.add_parser("run")
  rn.add_argument("--api", default=os.environ.get("API_HOST", "http://localhost:8000"))
  rn.add_argument("--qlog", required=True)
  args = p.parse_args()

  if args.cmd == "make-qlog":
    make_qlog(args.out)
    return 0
  return run(args.api, args.qlog)


if __name__ == "__main__":
  raise SystemExit(main())
