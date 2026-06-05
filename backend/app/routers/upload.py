"""Upload-URL issuance — GET /v1.4/{dongle_id}/upload_url/.

Mirrors `system/loggerd/uploader.py:143`. The device asks for a destination for
a relative `path` (e.g. `0000007b--abc1234567--3/qlog.zst`); we presign a direct
PUT into object storage and return `{url, headers}`. Status 412 tells the device
we already have the file, so it marks it done and skips.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import authenticated_device
from ..db import get_session
from ..models import Device, Upload
from ..naming import parse_upload_path
from ..schemas import UploadUrlResponse
from ..storage import build_key, head_object, presign_put

router = APIRouter(tags=["device"])
log = logging.getLogger("capypilot.upload")

# Content types for the artifacts we know about.
_CONTENT_TYPES = {
  ".ts": "video/mp2t",
  ".hevc": "video/hevc",
  ".zst": "application/zstd",
  ".bz2": "application/x-bzip2",
}


def _content_type(filename: str) -> str:
  for ext, ct in _CONTENT_TYPES.items():
    if filename.endswith(ext):
      return ct
  return "application/octet-stream"


@router.get("/v1.4/{dongle_id}/upload_url/", response_model=UploadUrlResponse)
@router.get("/v1.4/{dongle_id}/upload_url", response_model=UploadUrlResponse)
async def upload_url(
  path: str = Query(...),
  device: Device = Depends(authenticated_device),
  session: AsyncSession = Depends(get_session),
) -> UploadUrlResponse | Response:
  parsed = parse_upload_path(path)
  if parsed is None:
    # boot/ and crash/ logs, or anything not a segment artifact: store verbatim
    # under the device prefix so nothing is lost, but don't try to index it.
    key = f"{device.dongle_id}/{path.strip().lstrip('/')}"
  else:
    key = build_key(device.dongle_id, parsed.log_id, str(parsed.segment_num), parsed.filename)

  # Already have it? Tell the device to skip (412).
  if await head_object(key) is not None:
    log.info("upload_url 412 (exists) %s", key)
    return Response(status_code=412)

  url, headers = presign_put(key, content_type=_content_type(key))
  session.add(
    Upload(
      dongle_id=device.dongle_id,
      key=key,
      device_path=path,
      status="requested",
      created_at=datetime.now(timezone.utc),
    )
  )
  log.info("upload_url issued %s", key)
  return UploadUrlResponse(url=url, headers=headers)
