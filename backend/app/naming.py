"""Route / segment / upload-path parsing.

Mirrors `tools/lib/helpers.py` (RE) and `tools/lib/route.py` so our canonical
names match openpilot tooling exactly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

DONGLE_ID = r"(?P<dongle_id>[a-f0-9]{16})"
TIMESTAMP = r"(?P<timestamp>[0-9]{4}-[0-9]{2}-[0-9]{2}--[0-9]{2}-[0-9]{2}-[0-9]{2})"
LOG_ID_V2 = r"(?P<count>[a-f0-9]{8})--(?P<uid>[a-z0-9]{10})"
LOG_ID = rf"(?P<log_id>(?:{TIMESTAMP}|{LOG_ID_V2}))"
ROUTE_NAME = rf"(?P<route_name>{DONGLE_ID}[|_/]{LOG_ID})"
SEGMENT_NAME = rf"{ROUTE_NAME}(?:--|/)(?P<segment_num>[0-9]+)"

# A device segment directory has no dongle prefix, e.g. "0000007b--abc1234567--3"
# or legacy "2024-05-30--14-25-18--3".
SEG_DIR = re.compile(rf"^(?P<log_id>(?:{TIMESTAMP}|{LOG_ID_V2}))--(?P<segment_num>[0-9]+)$")


@dataclass(frozen=True)
class UploadPath:
  log_id: str          # "0000007b--abc1234567" or "2024-05-30--14-25-18"
  segment_num: int     # 3
  filename: str        # "qlog.zst"
  segment_dir: str     # "0000007b--abc1234567--3"


def parse_upload_path(path: str) -> UploadPath | None:
  """Parse the device's `path` query param "{segment_dir}/{filename}".

  Returns None for anything that isn't a recognizable segment artifact (e.g.
  boot/ or crash/ logs, which we accept but don't index as routes).
  """
  path = path.strip().lstrip("/")
  if "/" not in path:
    return None
  segment_dir, filename = path.rsplit("/", 1)
  m = SEG_DIR.match(segment_dir)
  if not m:
    return None
  return UploadPath(
    log_id=m.group("log_id"),
    segment_num=int(m.group("segment_num")),
    filename=filename,
    segment_dir=segment_dir,
  )


def route_id(dongle_id: str, log_id: str) -> str:
  """Canonical route name: '{dongle_id}|{log_id}' (matches RouteName)."""
  return f"{dongle_id}|{log_id}"


_ROUTE_RE = re.compile(rf"^{ROUTE_NAME}$")


def split_route_id(canonical: str) -> tuple[str, str] | None:
  """Split '{dongle_id}|{log_id}' (or with '/'/'_' separator) -> (dongle_id, log_id)."""
  m = _ROUTE_RE.match(canonical)
  if not m:
    return None
  return m.group("dongle_id"), m.group("log_id")


# Map a stored filename to its segment "kind" slot (see models.SEGMENT_FILE_KINDS).
def file_kind(filename: str) -> str | None:
  base = filename.lower()
  if base.startswith("qlog"):
    return "qlog"
  if base.startswith("rlog"):
    return "rlog"
  if base == "qcamera.ts":
    return "qcamera"
  if base == "fcamera.hevc":
    return "fcamera"
  if base == "ecamera.hevc":
    return "ecamera"
  if base == "dcamera.hevc":
    return "dcamera"
  return None
