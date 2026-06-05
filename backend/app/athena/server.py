"""Athena WebSocket server — the device control channel (ATHENA_HOST).

Implements `wss://host/ws/v2/{dongle_id}`. The device authenticates with a `jwt`
cookie, then speaks JSON-RPC 2.0 (see `system/athena/athenad.py`):

  - device -> us: `forwardLogs`, `storeStats` requests (we persist + ack)
  - us -> device: commands like `listDataDirectory`, `uploadFilesToUrls`
    (we send a request, device replies; the Registry matches the reply)

A small HTTP surface lets the API/UI trigger commands and read online state.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ..auth import AuthError, verify_with_public_key
from ..config import get_settings
from ..db import SessionLocal
from ..deps import current_user
from ..models import Device, DeviceStat, User
from .registry import CommandError, DeviceConnection, registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("capypilot.athena")

app = FastAPI(title="capypilot athena")

# connect calls this service cross-origin with an `Authorization: JWT` header.
_origins = get_settings().cors_origin_list()
app.add_middleware(
  CORSMiddleware,
  allow_origins=_origins,
  allow_credentials=("*" not in _origins),
  allow_methods=["*"],
  allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
  return {"status": "ok", "online": registry.online_ids()}


async def _set_online(dongle_id: str, online: bool) -> None:
  async with SessionLocal() as session:
    device = await session.get(Device, dongle_id)
    if device is not None:
      device.is_online = online
      device.last_seen = datetime.now(timezone.utc)
      await session.commit()


async def _store_stat(dongle_id: str, kind: str, payload: dict) -> None:
  async with SessionLocal() as session:
    session.add(DeviceStat(dongle_id=dongle_id, kind=kind, payload=payload))
    await session.commit()


async def _authenticate(websocket: WebSocket, dongle_id: str) -> Device | None:
  token = websocket.cookies.get("jwt")
  if not token:
    return None
  async with SessionLocal() as session:
    device = await session.get(Device, dongle_id)
  if device is None:
    return None
  try:
    claims = verify_with_public_key(token, device.public_key, device.jwt_algorithm)
  except AuthError:
    return None
  if claims.get("identity") != dongle_id:
    return None
  return device


async def _handle_request(dongle_id: str, msg: dict) -> dict:
  """Handle a device-initiated JSON-RPC request; return the response object."""
  method = msg.get("method")
  params = msg.get("params") or {}
  msg_id = msg.get("id")
  result: dict = {"success": True}
  try:
    if method == "forwardLogs":
      await _store_stat(dongle_id, "logs", {"logs": params.get("logs", "")})
    elif method == "storeStats":
      await _store_stat(dongle_id, "stats", {"stats": params.get("stats", "")})
    elif method == "echo":
      result = params
    else:
      return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"method '{method}' not handled"}}
  except Exception as e:  # ack with failure rather than dropping the connection
    log.exception("athena request handler error")
    result = {"success": False, "error": str(e)}
  return {"jsonrpc": "2.0", "id": msg_id, "result": result}


@app.websocket("/ws/v2/{dongle_id}")
async def ws_v2(websocket: WebSocket, dongle_id: str) -> None:
  device = await _authenticate(websocket, dongle_id)
  if device is None:
    await websocket.close(code=4401)  # unauthorized
    return

  await websocket.accept()
  conn = DeviceConnection(dongle_id=dongle_id, ws=websocket)
  registry.add(conn)
  await _set_online(dongle_id, True)
  log.info("device %s connected", dongle_id)

  try:
    while True:
      raw = await websocket.receive_text()
      try:
        msg = json.loads(raw)
      except json.JSONDecodeError:
        continue
      if not isinstance(msg, dict):
        continue

      if "method" in msg:
        response = await _handle_request(dongle_id, msg)
        await websocket.send_text(json.dumps(response))
      elif "id" in msg and ("result" in msg or "error" in msg):
        conn.resolve(msg["id"], msg.get("result"), msg.get("error"))
  except WebSocketDisconnect:
    pass
  except Exception:
    log.exception("athena ws loop error for %s", dongle_id)
  finally:
    registry.remove(dongle_id)
    await _set_online(dongle_id, False)
    log.info("device %s disconnected", dongle_id)


# ── HTTP control surface (used by the API/UI to drive a live device) ─────────
class CallRequest(BaseModel):
  method: str
  params: dict = {}
  timeout: float = 30.0


@app.post("/devices/{dongle_id}/call")
async def call_device(dongle_id: str, body: CallRequest) -> dict:
  """Internal control surface (no auth; reached only on the compose network).

  Used by the API to drive a device — upload requests, nav destinations, etc.
  """
  conn = registry.get(dongle_id)
  if conn is None:
    return {"online": False, "error": "device not connected"}
  try:
    result = await conn.call(body.method, body.params, timeout=body.timeout)
    return {"online": True, "result": result}
  except CommandError as e:
    return {"online": True, "error": str(e)}
  except TimeoutError:
    return {"online": True, "error": "timeout waiting for device"}


# Default per-command wait; device-side ops like takeSnapshot can be slow.
_RPC_TIMEOUT = 45.0


@app.post("/{dongle_id}")
@app.post("/{dongle_id}/")
async def athena_jsonrpc(
  dongle_id: str, request: Request, _: User = Depends(current_user)
) -> dict:
  """commaai/connect's Athena surface: `POST {ATHENA_URL}/{dongle_id}`.

  Connect (and the comma mobile app) send a JSON-RPC 2.0 request here to control
  a live device — `takeSnapshot`, `getNetworkType`, `getNetworks`, `reboot`,
  `listDataDirectory`, `getMessage`, etc. We relay it verbatim to the connected
  device over its WebSocket and return the device's reply as a JSON-RPC envelope
  (the device's athenad implements the methods; this is a pass-through). User is
  authenticated via the `Authorization: JWT` header.
  """
  try:
    payload = await request.json()
  except Exception:
    return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}

  method = payload.get("method")
  params = payload.get("params") or {}
  msg_id = payload.get("id", 0)
  if not method:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32600, "message": "missing method"}}

  conn = registry.get(dongle_id)
  if conn is None:
    # Connect treats a missing `result` as "device offline" and degrades.
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": 4040, "message": "device not registered or not connected"}}

  timeout = float(payload.get("timeout", _RPC_TIMEOUT))
  try:
    result = await conn.call(method, params, timeout=timeout)
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}
  except CommandError as e:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": str(e)}}
  except TimeoutError:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32001, "message": "timeout waiting for device"}}
