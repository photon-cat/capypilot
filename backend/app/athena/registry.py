"""In-memory registry of live device WebSocket connections.

Lets the API/UI issue JSON-RPC commands to a connected device and await the
reply. Single-process; for multi-replica deployments back this with Redis pub/sub
(documented as future work).
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field


class CommandError(Exception):
  pass


@dataclass
class DeviceConnection:
  dongle_id: str
  ws: object  # starlette WebSocket
  _seq: int = 0
  _pending: dict[str, asyncio.Future] = field(default_factory=dict)
  _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

  def next_id(self) -> str:
    self._seq += 1
    return f"cmd-{self._seq}"

  async def call(self, method: str, params: dict | None = None, timeout: float = 30.0):
    """Send a JSON-RPC request to the device and await its result."""
    msg_id = self.next_id()
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    self._pending[msg_id] = fut
    payload = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": msg_id}
    async with self._send_lock:
      await self.ws.send_text(json.dumps(payload))
    try:
      return await asyncio.wait_for(fut, timeout=timeout)
    finally:
      self._pending.pop(msg_id, None)

  def resolve(self, msg_id: str, result, error) -> None:
    fut = self._pending.get(msg_id)
    if fut is None or fut.done():
      return
    if error is not None:
      fut.set_exception(CommandError(str(error)))
    else:
      fut.set_result(result)


class Registry:
  def __init__(self) -> None:
    self._conns: dict[str, DeviceConnection] = {}

  def add(self, conn: DeviceConnection) -> None:
    self._conns[conn.dongle_id] = conn

  def remove(self, dongle_id: str) -> None:
    self._conns.pop(dongle_id, None)

  def get(self, dongle_id: str) -> DeviceConnection | None:
    return self._conns.get(dongle_id)

  def online_ids(self) -> list[str]:
    return list(self._conns.keys())


registry = Registry()
