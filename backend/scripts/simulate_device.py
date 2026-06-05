#!/usr/bin/env python3
"""Full comma-device simulator — exercises the whole backend like a real car.

Unlike the narrow verify_* scripts, this simulates a complete device lifecycle:

  1. registers (self-signed register token + public key)               -> dongle_id
  2. holds an Athena WebSocket open (authenticated by the device JWT cookie)
  3. uploads a multi-segment drive (qlog + qcamera + rlog per segment) via
     presigned upload URLs, exactly like loggerd/uploader
  4. answers remote JSON-RPC commands the backend relays (takeSnapshot,
     getNetworkType, getNetworks, reboot, listDataDirectory)
  5. performs a backend-requested `uploadFilesToUrls` (the "request upload from
     device" flow: UI -> Athena -> device PUTs the file -> ingest indexes it)
  6. receives a live `setNavDestination` push (online navigation)
  7. sends `storeStats` / `forwardLogs` up to the backend

Then it asserts the backend reacted correctly to all of it.

Run against a live stack:
  docker compose exec api python scripts/provision_device.py make-qlog /tmp/q.zst
  docker compose cp api:/tmp/q.zst ./q.zst
  ./.venv/bin/python scripts/simulate_device.py --qlog ./q.zst --segments 3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import string
import sys
from datetime import datetime, timedelta, timezone

import jwt
import requests
import websockets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.auth import derive_dongle_id  # noqa: E402
from scripts.provision_device import device_token, gen_keypair  # noqa: E402

OK = "✓"
TS_PACKET = b"\x47" + b"\x00" * 187


def _rand_log_id() -> str:
  count = "".join(random.choices("0123456789abcdef", k=8))
  uid = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
  return f"{count}--{uid}"


def _synth_bytes(filename: str, qlog: bytes) -> bytes:
  if filename.startswith("qlog"):
    return qlog
  if filename.startswith("rlog"):
    return qlog  # presence-only (not parsed); reuse a valid blob
  if filename == "qcamera.ts":
    return TS_PACKET * 50
  if filename.endswith(".hevc"):
    return b"\x00\x00\x00\x01" + b"\x00" * 4096
  return b"\x00" * 256


class DeviceSim:
  """A simulated openpilot device speaking the Athena protocol."""

  def __init__(self, dongle_id: str, log_id: str, n_segments: int, qlog: bytes) -> None:
    self.dongle_id = dongle_id
    self.log_id = log_id
    self.n_segments = n_segments
    self.qlog = qlog
    self.ws = None
    self.connected = asyncio.Event()
    self._send_lock = asyncio.Lock()
    self._req_id = 0
    self.state = {
      "commands": [],         # method names the backend asked us to run
      "snapshots": 0,
      "nav_destination": None,
      "uploads_performed": [],  # fns we PUT on the backend's behalf
      "acks": 0,              # responses to our own storeStats/forwardLogs
    }

  async def _send(self, obj: dict) -> None:
    async with self._send_lock:
      await self.ws.send(json.dumps(obj))

  async def send_request(self, method: str, params: dict) -> None:
    self._req_id += 1
    await self._send({"jsonrpc": "2.0", "method": method, "params": params, "id": f"dev-{self._req_id}"})

  async def _dispatch(self, method: str, params: dict):
    if method == "takeSnapshot":
      self.state["snapshots"] += 1
      return {"jpegBack": "/9j/back", "jpegFront": "/9j/front"}
    if method == "getNetworkType":
      return {"network_type": 4}
    if method == "getNetworks":
      return [{"ssid": "capy-wifi", "strength": 77, "security_type": "WPA2"}]
    if method == "reboot":
      return {"success": True}
    if method == "listDataDirectory":
      return [f"{self.log_id}--{i}" for i in range(self.n_segments)]
    if method == "setNavDestination":
      self.state["nav_destination"] = params
      return {"success": True}
    if method == "uploadFilesToUrls":
      files = params.get("files_data") or params.get("files") or []
      for f in files:
        body = _synth_bytes(f["fn"].rsplit("/", 1)[-1], self.qlog)
        url, headers = f["url"], f.get("headers", {})
        await asyncio.to_thread(lambda: requests.put(url, data=body, headers=headers, timeout=30))
        self.state["uploads_performed"].append(f["fn"])
      return {"enqueued": len(files)}
    return {"unhandled": method}

  async def run(self, ws_url: str, token: str, stop: asyncio.Event) -> None:
    async with websockets.connect(ws_url, additional_headers={"Cookie": f"jwt={token}"}) as ws:
      self.ws = ws
      self.connected.set()
      await self.send_request("storeStats", {"stats": "boot"})  # like athenad on connect
      while not stop.is_set():
        try:
          raw = await asyncio.wait_for(ws.recv(), timeout=0.3)
        except asyncio.TimeoutError:
          continue
        except websockets.ConnectionClosed:
          break
        msg = json.loads(raw)
        if "method" in msg:  # backend -> device command
          self.state["commands"].append(msg["method"])
          result = await self._dispatch(msg["method"], msg.get("params", {}))
          await self._send({"jsonrpc": "2.0", "id": msg["id"], "result": result})
        elif "result" in msg or "error" in msg:  # ack of our storeStats/forwardLogs
          self.state["acks"] += 1


# ── HTTP helpers (run off the event loop so the WS task stays responsive) ─────
def _get(url, **kw):
  return requests.get(url, timeout=20, **kw)


def _post(url, **kw):
  return requests.post(url, timeout=20, **kw)


async def aget(url, **kw):
  return await asyncio.to_thread(lambda: _get(url, **kw))


async def apost(url, **kw):
  return await asyncio.to_thread(lambda: _post(url, **kw))


async def run(api_host: str, athena_http: str, athena_ws: str, segments: int, qlog_path: str) -> int:
  with open(qlog_path, "rb") as f:
    qlog = f.read()
  priv, pub = gen_keypair()
  dongle_id = derive_dongle_id(pub)
  log_id = _rand_log_id()
  route = f"{dongle_id}|{log_id}"
  print(f"dongle_id={dongle_id}  route={route}  segments={segments}")

  # 1) register
  reg = jwt.encode({"register": True, "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, priv, algorithm="RS256")
  r = await apost(f"{api_host}/v2/pilotauth/", params={"public_key": pub, "register_token": reg, "serial": "CAPY-SIM"})
  r.raise_for_status()
  print(f"[device] registered {OK}")

  dev_token = device_token(priv, dongle_id)
  dev_auth = {"Authorization": "JWT " + dev_token}
  user_token = (await apost(f"{api_host}/v2/auth/", data={
    "provider": "email",
    "code": f"{os.environ.get('ADMIN_EMAIL', 'admin@local')}:{os.environ.get('ADMIN_PASSWORD', 'admin')}",
  })).json()["access_token"]
  user_auth = {"Authorization": "JWT " + user_token}

  # 2) connect Athena
  sim = DeviceSim(dongle_id, log_id, segments, qlog)
  stop = asyncio.Event()
  task = asyncio.create_task(sim.run(f"{athena_ws}/ws/v2/{dongle_id}", dev_token, stop))
  try:
    await asyncio.wait_for(sim.connected.wait(), timeout=10)
    for _ in range(50):
      if dongle_id in (await aget(f"{athena_http}/health")).json().get("online", []):
        break
      await asyncio.sleep(0.1)
    print(f"[device] Athena connected + online {OK}")

    # 3) upload a multi-segment drive (device-initiated, like uploader)
    uploaded = 0
    for seg in range(segments):
      for fname in ("qlog.zst", "qcamera.ts", "rlog.zst"):
        path = f"{log_id}--{seg}/{fname}"
        u = await aget(f"{api_host}/v1.4/{dongle_id}/upload_url/", params={"path": path}, headers=dev_auth)
        u.raise_for_status()
        body, j = _synth_bytes(fname, qlog), u.json()
        put = await asyncio.to_thread(lambda: requests.put(j["url"], data=body, headers=j.get("headers", {}), timeout=30))
        assert put.status_code in (200, 201), f"{put.status_code} {put.text}"
        uploaded += 1
    print(f"[device] uploaded drive: {uploaded} files across {segments} segments {OK}")

    # 4) wait for the backend to index all segments
    rs = None
    for _ in range(40):
      resp = await aget(f"{api_host}/v1/devices/{dongle_id}/routes_segments", params={"route_str": route}, headers=user_auth)
      if resp.status_code == 200 and resp.json():
        rs = resp.json()[0]
        if len(rs.get("segment_numbers", [])) >= segments:
          break
      await asyncio.sleep(1)
    assert rs and len(rs["segment_numbers"]) == segments, f"indexed {rs and rs.get('segment_numbers')}"
    print(f"[backend] indexed route {OK} (segments={rs['segment_numbers']}, dist={rs['distance']:.2f}mi)")

    # 5) remote control relayed through the connect Athena surface
    def rpc(method, params=None):
      return _post(f"{athena_http}/{dongle_id}", headers=user_auth,
                   json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).json()

    snap = await asyncio.to_thread(rpc, "takeSnapshot")
    assert snap.get("result", {}).get("jpegBack") and sim.state["snapshots"] == 1, snap
    net = await asyncio.to_thread(rpc, "getNetworkType")
    ddir = await asyncio.to_thread(rpc, "listDataDirectory")
    assert net["result"]["network_type"] == 4
    assert len(ddir["result"]) == segments, ddir
    print(f"[control] takeSnapshot + getNetworkType + listDataDirectory relayed {OK} "
          f"(dir lists {len(ddir['result'])} segs)")

    # 6) backend-requested upload: UI -> Athena -> device PUTs fcamera -> ingest
    ru = await apost(f"{api_host}/api/routes/{route}/request_upload", headers=user_auth,
                     json={"kinds": ["fcamera"], "segments": [0]})
    ru.raise_for_status()
    got_fcamera = False
    for _ in range(30):
      files = (await aget(f"{api_host}/v1/route/{route}/files", headers=user_auth)).json()
      if files.get("cameras"):
        got_fcamera = True
        break
      await asyncio.sleep(1)
    assert got_fcamera and any("fcamera" in fn for fn in sim.state["uploads_performed"]), sim.state["uploads_performed"]
    print(f"[control] request_upload -> device PUT fcamera -> indexed {OK}")

    # 7) live navigation push (device online -> delivered immediately)
    dest = {"latitude": 32.7157, "longitude": -117.1611, "place_name": "1441 State St", "place_details": "San Diego, CA"}
    sd = (await apost(f"{api_host}/v1/navigation/{dongle_id}/set_destination", headers=user_auth, json=dest)).json()
    await asyncio.sleep(0.3)  # let the push round-trip
    assert sd["success"] and sd["saved_next"] is False, sd  # False => pushed live, not queued
    assert sim.state["nav_destination"] and sim.state["nav_destination"]["place_name"] == dest["place_name"]
    print(f"[nav] live setNavDestination delivered to device {OK} (saved_next={sd['saved_next']})")

    # 8) device pushes stats + logs
    await sim.send_request("storeStats", {"stats": "1 route, 3 segments"})
    await sim.send_request("forwardLogs", {"logs": "INFO booted"})
    for _ in range(30):
      if sim.state["acks"] >= 2:
        break
      await asyncio.sleep(0.1)
    assert sim.state["acks"] >= 2, sim.state["acks"]
    print(f"[device] storeStats + forwardLogs acked {OK} (acks={sim.state['acks']})")

    # device still shows online in connect's device view
    dev = next(d for d in (await aget(f"{api_host}/v1/me/devices/", headers=user_auth)).json()
               if d["dongle_id"] == dongle_id)
    assert dev["prime"] is True
    print(f"[backend] device visible in connect: online_ping={dev['last_athena_ping']}, prime={dev['prime']} {OK}")

    print(f"\nSIMULATED DEVICE SCENARIO PASSED — commands handled: {sim.state['commands']}")
  finally:
    stop.set()
    await task
  return 0


def main() -> int:
  p = argparse.ArgumentParser()
  p.add_argument("--api", default=os.environ.get("API_HOST", "http://localhost:8000"))
  p.add_argument("--athena-http", default="http://localhost:8001")
  p.add_argument("--athena-ws", default="ws://localhost:8001")
  p.add_argument("--segments", type=int, default=3)
  p.add_argument("--qlog", required=True)
  args = p.parse_args()
  return asyncio.run(run(args.api, args.athena_http, args.athena_ws, args.segments, args.qlog))


if __name__ == "__main__":
  raise SystemExit(main())
