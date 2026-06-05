"""Read/serve API — tooling-compatible route metadata + file listing.

These mirror comma's endpoints so openpilot's own `tools/lib/route.py` and
`LogReader` work unmodified against this backend:

  GET /v1/route/{route}/files   -> {kind: [presigned GET urls]}
  GET /v1/route/{route}         -> route metadata (maxqlog, times, ...)
  GET /v1/me                    -> authenticated user
  GET /v1/devices               -> user's devices

The file-URL *paths* deliberately end in `/{dongle_id}/{log_id}/{seg}/{fn}` so
`tools/lib/route.py:73`'s `path.rsplit('/', 4)` parses them correctly.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import current_user
from ..models import Device, Route, Segment, User
from ..storage import presign_get

router = APIRouter(tags=["data"])

# kind -> the JSON key comma-style tooling groups by (values are flattened, so
# the exact key names are cosmetic, but we keep them recognizable).
_KIND_TO_LISTKEY = {
  "rlog": "logs",
  "qlog": "qlogs",
  "fcamera": "cameras",
  "dcamera": "dcameras",
  "ecamera": "ecameras",
  "qcamera": "qcameras",
}


async def _load_route(session: AsyncSession, route_name: str) -> Route:
  route = await session.get(Route, route_name)
  if route is None:
    raise HTTPException(status_code=404, detail="unknown route")
  return route


@router.get("/v1/route/{route_name}/files")
async def route_files(
  route_name: str = Path(...),
  session: AsyncSession = Depends(get_session),
  _: User = Depends(current_user),
) -> dict[str, list[str]]:
  await _load_route(session, route_name)
  segs = (
    await session.scalars(
      select(Segment).where(Segment.route_id == route_name).order_by(Segment.segment_num)
    )
  ).all()

  out: dict[str, list[str]] = {key: [] for key in _KIND_TO_LISTKEY.values()}
  for seg in segs:
    for kind, meta in (seg.files or {}).items():
      listkey = _KIND_TO_LISTKEY.get(kind)
      if listkey and meta.get("key"):
        out[listkey].append(presign_get(meta["key"]))
  return out


@router.get("/v1/route/{route_name}")
async def route_meta(
  route_name: str = Path(...),
  session: AsyncSession = Depends(get_session),
  _: User = Depends(current_user),
) -> dict:
  route = await _load_route(session, route_name)
  return {
    "fullname": route.id,
    "dongle_id": route.dongle_id,
    "maxqlog": route.max_segment,
    "segment_numbers": list(range(route.max_segment + 1)) if route.max_segment >= 0 else [],
    "start_time": route.start_time_utc.isoformat() if route.start_time_utc else None,
    "end_time": route.end_time_utc.isoformat() if route.end_time_utc else None,
    "length_m": route.length_m,
    "version": route.version,
    "git_commit": route.git_commit,
  }


@router.get("/v1/me")
async def me(user: User = Depends(current_user)) -> dict:
  return {"id": str(user.id), "email": user.email, "username": user.email, "superuser": True}


@router.get("/v1/devices")
@router.get("/v1.1/devices")
async def list_devices(
  session: AsyncSession = Depends(get_session),
  _: User = Depends(current_user),
) -> list[dict]:
  devices = (await session.scalars(select(Device).order_by(Device.created_at))).all()
  return [
    {
      "dongle_id": d.dongle_id,
      "alias": d.alias,
      "serial": d.serial,
      "is_online": d.is_online,
      "last_seen": d.last_seen.isoformat() if d.last_seen else None,
    }
    for d in devices
  ]
