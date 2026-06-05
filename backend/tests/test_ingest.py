"""Ingest helper tests (pure) + a capnp-gated round-trip parse test."""
import importlib.util

import pytest

from app.ingest.worker import _haversine_length, _split_key

capnp_available = importlib.util.find_spec("capnp") is not None


def test_split_key_rejects_short():
  assert _split_key("only/two/parts") is None  # needs >=4 and digit segment
  assert _split_key("dongle/log/notnum/qlog.zst") is None


def test_haversine_length_zero_and_known():
  assert _haversine_length([(0.0, 0.0)]) == 0.0
  # ~111km per degree of latitude at the equator
  d = _haversine_length([(0.0, 0.0), (1.0, 0.0)])
  assert 110_000 < d < 112_000


@pytest.mark.skipif(not capnp_available, reason="pycapnp not installed (runs in container)")
def test_parse_segment_roundtrip(tmp_path):
  """Build a synthetic qlog and parse it back."""
  import os
  import time

  import capnp
  import zstandard as zstd

  from app.config import get_settings
  from app.ingest.parser import parse_segment

  capnp.remove_import_hook()
  log = capnp.load(os.path.join(get_settings().cereal_path, "log.capnp"))

  blobs = []
  init = log.Event.new_message()
  init.logMonoTime = 0
  idat = init.init("initData")
  idat.dongleId = "0123456789abcdef"
  idat.version = "test"
  idat.wallTimeNanos = int(time.time() * 1e9)
  blobs.append(init.to_bytes())

  base_ms = int(time.time() * 1000)
  for i in range(10):
    e = log.Event.new_message()
    e.logMonoTime = i * 1_000_000_000
    g = e.init("gpsLocationExternal")
    g.latitude = 37.0 + i * 0.001
    g.longitude = -122.0
    g.speed = 10.0
    g.bearingDeg = 90.0
    g.unixTimestampMillis = base_ms + i * 1000
    g.hasFix = True
    g.flags = 1
    blobs.append(e.to_bytes())

  blob = zstd.compress(b"".join(blobs), 10)
  parsed = parse_segment(blob, decimate_to=60)
  assert parsed.version == "test"
  assert parsed.dongle_id == "0123456789abcdef"
  assert len(parsed.gps) == 10
  assert abs(parsed.gps[0].lat - 37.0) < 1e-6
