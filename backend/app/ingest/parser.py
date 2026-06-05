"""qlog/rlog parser.

Loads the official `cereal/log.capnp` schema with pycapnp (mirroring
`cereal/__init__.py`) and decodes the bzip2/zstd-compressed capnp `Event`
stream exactly like `tools/lib/logreader.py`. We extract just what the backend
indexes: route/segment metadata (InitData, Sentinel) and a decimated GPS trace.

`capnp` is imported lazily so the rest of the app runs without it installed
(only the ingest worker needs it).
"""
from __future__ import annotations

import bz2
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache

import zstandard as zstd

from ..config import get_settings

# Compression magic bytes (see tools/lib/logreader.py:42-49).
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
_BZ2_MAGIC = b"BZh"


@lru_cache
def _load_schema():
  """Load log.capnp once. Imports resolve relative to the schema directory."""
  import capnp  # lazy

  capnp.remove_import_hook()
  schema_dir = get_settings().cereal_path
  return capnp.load(os.path.join(schema_dir, "log.capnp"))


def decompress(data: bytes) -> bytes:
  if data[:4] == _ZSTD_MAGIC:
    # stream_reader handles multi-frame content and unknown frame sizes.
    import io

    dctx = zstd.ZstdDecompressor()
    with dctx.stream_reader(io.BytesIO(data), read_across_frames=True) as reader:
      return reader.read()
  if data[:3] == _BZ2_MAGIC:
    return bz2.decompress(data)
  return data  # already raw capnp


def iter_events(raw_or_compressed: bytes):
  """Yield capnp Event readers from a (possibly compressed) log blob."""
  log = _load_schema()
  data = decompress(raw_or_compressed)
  yield from log.Event.read_multiple_bytes(data)


@dataclass
class GpsSample:
  t_utc: datetime
  lat: float
  lon: float
  alt: float | None
  speed: float | None
  bearing: float | None


@dataclass
class ParsedSegment:
  dongle_id: str | None = None
  log_id: str | None = None
  version: str | None = None
  git_commit: str | None = None
  init_params: dict | None = None
  wall_time_utc: datetime | None = None
  start_mono: int | None = None
  end_mono: int | None = None
  gps: list[GpsSample] = field(default_factory=list)


def _read_gps(loc) -> GpsSample | None:
  # Accept either gpsLocation or gpsLocationExternal (same GpsLocationData shape).
  lat = float(loc.latitude)
  lon = float(loc.longitude)
  if lat == 0.0 and lon == 0.0:
    return None
  # `flags`/`hasFix` indicate a valid fix; require a plausible fix.
  has_fix = getattr(loc, "hasFix", True)
  if has_fix is False and getattr(loc, "flags", 1) == 0:
    return None
  ts_ms = int(getattr(loc, "unixTimestampMillis", 0) or 0)
  if ts_ms <= 0:
    return None
  return GpsSample(
    t_utc=datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc),
    lat=lat,
    lon=lon,
    alt=float(getattr(loc, "altitude", 0.0)),
    speed=float(getattr(loc, "speed", 0.0)),
    bearing=float(getattr(loc, "bearingDeg", 0.0)),
  )


def parse_segment(blob: bytes, *, decimate_to: int | None = None) -> ParsedSegment:
  """Parse one segment's qlog/rlog blob into indexable metadata + GPS trace."""
  result = ParsedSegment()
  raw_gps: list[GpsSample] = []

  for evt in iter_events(blob):
    which = evt.which()
    mono = int(evt.logMonoTime)
    if result.start_mono is None:
      result.start_mono = mono
    result.end_mono = mono

    if which == "initData":
      init = evt.initData
      result.dongle_id = str(init.dongleId) or None
      result.version = str(init.version) or None
      result.git_commit = str(init.gitCommit) or None
      wt = int(getattr(init, "wallTimeNanos", 0) or 0)
      if wt > 0:
        result.wall_time_utc = datetime.fromtimestamp(wt / 1e9, tz=timezone.utc)
      # InitData.params is a Map(Text, Data); capture a small, text-safe subset.
      try:
        params = {}
        for entry in init.params.entries:
          key = str(entry.key)
          if key in ("CarParams", "GitBranch", "GitRemote", "DongleId", "Version"):
            continue  # skip binary / redundant
          try:
            params[key] = bytes(entry.value).decode("utf-8", "ignore")[:256]
          except Exception:
            pass
        result.init_params = params or None
      except Exception:
        result.init_params = None
    elif which in ("gpsLocationExternal", "gpsLocation"):
      sample = _read_gps(getattr(evt, which))
      if sample is not None:
        raw_gps.append(sample)

  # Decimate the GPS trace evenly for a lightweight map overlay.
  if decimate_to and len(raw_gps) > decimate_to:
    step = len(raw_gps) / decimate_to
    result.gps = [raw_gps[int(i * step)] for i in range(decimate_to)]
  else:
    result.gps = raw_gps
  return result
