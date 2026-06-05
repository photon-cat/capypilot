"""Athena relay-core tests: DeviceConnection request/response matching + registry.

These cover the mechanism that powers live device control (snapshot, network
type, reboot, …) without needing a real device or DB.
"""
import asyncio
import json

import pytest

from app.athena.registry import CommandError, DeviceConnection, Registry


class FakeWS:
  def __init__(self) -> None:
    self.sent: list[str] = []

  async def send_text(self, text: str) -> None:
    self.sent.append(text)


async def _wait_sent(ws: FakeWS) -> dict:
  for _ in range(200):
    if ws.sent:
      return json.loads(ws.sent[-1])
    await asyncio.sleep(0.001)
  raise AssertionError("device connection never sent a request")


async def test_call_relays_request_and_resolves_result():
  ws = FakeWS()
  conn = DeviceConnection(dongle_id="d", ws=ws)
  task = asyncio.create_task(conn.call("takeSnapshot", {}, timeout=5))
  msg = await _wait_sent(ws)
  assert msg["method"] == "takeSnapshot" and msg["jsonrpc"] == "2.0" and "id" in msg
  conn.resolve(msg["id"], {"jpegBack": "abc"}, None)
  assert await task == {"jpegBack": "abc"}


async def test_call_error_raises_command_error():
  ws = FakeWS()
  conn = DeviceConnection(dongle_id="d", ws=ws)
  task = asyncio.create_task(conn.call("reboot", {}, timeout=5))
  msg = await _wait_sent(ws)
  conn.resolve(msg["id"], None, {"code": -1, "message": "nope"})
  with pytest.raises(CommandError):
    await task


async def test_call_times_out_when_no_reply():
  conn = DeviceConnection(dongle_id="d", ws=FakeWS())
  with pytest.raises((TimeoutError, asyncio.TimeoutError)):
    await conn.call("x", {}, timeout=0.05)


async def test_concurrent_calls_match_by_id():
  ws = FakeWS()
  conn = DeviceConnection(dongle_id="d", ws=ws)
  t1 = asyncio.create_task(conn.call("a", {}, timeout=5))
  await _wait_sent(ws)
  id1 = json.loads(ws.sent[-1])["id"]
  t2 = asyncio.create_task(conn.call("b", {}, timeout=5))
  for _ in range(200):
    if len(ws.sent) >= 2:
      break
    await asyncio.sleep(0.001)
  id2 = json.loads(ws.sent[-1])["id"]
  assert id1 != id2
  # resolve out of order
  conn.resolve(id2, {"r": 2}, None)
  conn.resolve(id1, {"r": 1}, None)
  assert await t1 == {"r": 1}
  assert await t2 == {"r": 2}


def test_registry_add_get_remove():
  r = Registry()
  conn = DeviceConnection(dongle_id="abc", ws=FakeWS())
  r.add(conn)
  assert r.get("abc") is conn
  assert r.online_ids() == ["abc"]
  r.remove("abc")
  assert r.get("abc") is None
  assert r.online_ids() == []
