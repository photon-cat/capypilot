#!/usr/bin/env python3
"""Verify live device control end to end.

Registers a device, connects a *simulated* device over the Athena WebSocket
(answering JSON-RPC requests), then drives commands through connect's Athena
surface (`POST {ATHENA_URL}/{dongle_id}`) and asserts the replies are relayed
back. Also checks the offline path returns a graceful error.

Run against a live stack:
  ./.venv/bin/python scripts/verify_device_control.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import jwt
import requests
import websockets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.auth import derive_dongle_id  # noqa: E402
from scripts.provision_device import device_token, gen_keypair  # noqa: E402

OK = "✓"


def _responder(method: str, params: dict):
  """Stand in for openpilot's athenad: canned replies for the methods we drive."""
  if method == "takeSnapshot":
    return {"jpegBack": "/9j/back", "jpegFront": "/9j/front"}
  if method == "getNetworkType":
    return {"network_type": 4}  # wifi
  if method == "getNetworks":
    return [{"ssid": "capy-wifi", "strength": 80}]
  if method == "reboot":
    return {"success": True}
  return {"echoed": params}


async def _fake_device(ws_url: str, cookie: str, ready: asyncio.Event, stop: asyncio.Event) -> None:
  async with websockets.connect(ws_url, additional_headers={"Cookie": f"jwt={cookie}"}) as ws:
    ready.set()
    while not stop.is_set():
      try:
        raw = await asyncio.wait_for(ws.recv(), timeout=0.3)
      except asyncio.TimeoutError:
        continue
      except websockets.ConnectionClosed:
        break
      msg = json.loads(raw)
      if "method" in msg:
        reply = {"jsonrpc": "2.0", "id": msg["id"], "result": _responder(msg["method"], msg.get("params", {}))}
        await ws.send(json.dumps(reply))


def _rpc(athena_http: str, dongle_id: str, method: str, token: str, params: dict | None = None) -> dict:
  return requests.post(
    f"{athena_http}/{dongle_id}",
    headers={"Authorization": "JWT " + token},
    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    timeout=20,
  ).json()


async def _arpc(athena_http: str, dongle_id: str, method: str, token: str, params: dict | None = None) -> dict:
  # Run the blocking HTTP call off the event loop so the simulated-device task
  # can service the relayed WebSocket request concurrently.
  return await asyncio.to_thread(_rpc, athena_http, dongle_id, method, token, params)


async def run(api_host: str, athena_http: str, athena_ws: str) -> int:
  priv, pub = gen_keypair()
  dongle_id = derive_dongle_id(pub)
  print(f"dongle_id={dongle_id}")

  # register
  reg = jwt.encode({"register": True, "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, priv, algorithm="RS256")
  r = requests.post(f"{api_host}/v2/pilotauth/", params={"public_key": pub, "register_token": reg, "serial": "CAPY-WS"}, timeout=15)
  r.raise_for_status()
  print(f"register {OK}")

  # user token (connect's header scheme)
  tok = requests.post(f"{api_host}/v2/auth/", data={"provider": "email",
        "code": f"{os.environ.get('ADMIN_EMAIL', 'admin@local')}:{os.environ.get('ADMIN_PASSWORD', 'admin')}"}, timeout=15)
  tok.raise_for_status()
  user_token = tok.json()["access_token"]

  # offline first: no device connected -> graceful error, no result
  off = await _arpc(athena_http, dongle_id, "getNetworkType", user_token)
  assert "result" not in off and off.get("error"), off
  print(f"offline -> error envelope {OK} (code={off['error']['code']})")

  # connect a simulated device over the Athena WebSocket
  ready, stop = asyncio.Event(), asyncio.Event()
  dev_token = device_token(priv, dongle_id)
  dev_task = asyncio.create_task(_fake_device(f"{athena_ws}/ws/v2/{dongle_id}", dev_token, ready, stop))
  try:
    await asyncio.wait_for(ready.wait(), timeout=10)
    # let the server register the connection + mark online
    for _ in range(50):
      h = await asyncio.to_thread(lambda: requests.get(f"{athena_http}/health", timeout=5).json())
      if dongle_id in h.get("online", []):
        break
      await asyncio.sleep(0.1)
    print(f"device online {OK}")

    snap = await _arpc(athena_http, dongle_id, "takeSnapshot", user_token)
    assert snap.get("result", {}).get("jpegBack"), snap
    print(f"takeSnapshot {OK} (jpegBack relayed)")

    net = await _arpc(athena_http, dongle_id, "getNetworkType", user_token)
    assert net.get("result", {}).get("network_type") == 4, net
    print(f"getNetworkType {OK} (network_type={net['result']['network_type']})")

    nets = await _arpc(athena_http, dongle_id, "getNetworks", user_token)
    assert isinstance(nets.get("result"), list) and nets["result"], nets
    print(f"getNetworks {OK} ({len(nets['result'])} network(s))")

    rb = await _arpc(athena_http, dongle_id, "reboot", user_token)
    assert rb.get("result", {}).get("success") is True, rb
    print(f"reboot {OK}")

    # unauthenticated request is rejected
    noauth = await asyncio.to_thread(
      lambda: requests.post(f"{athena_http}/{dongle_id}",
                            json={"jsonrpc": "2.0", "id": 1, "method": "takeSnapshot"}, timeout=15)
    )
    assert noauth.status_code == 401, f"expected 401 without token, got {noauth.status_code}"
    print(f"no-auth -> 401 {OK}")
  finally:
    stop.set()
    await dev_task

  print("\nDEVICE CONTROL VERIFIED")
  return 0


def main() -> int:
  p = argparse.ArgumentParser()
  p.add_argument("--api", default=os.environ.get("API_HOST", "http://localhost:8000"))
  p.add_argument("--athena-http", default="http://localhost:8001")
  p.add_argument("--athena-ws", default="ws://localhost:8001")
  args = p.parse_args()
  return asyncio.run(run(args.api, args.athena_http, args.athena_ws))


if __name__ == "__main__":
  raise SystemExit(main())
