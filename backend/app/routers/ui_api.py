"""Web-UI JSON API (+ login). Expanded in Phase 5 with route detail + map data."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import httpx
from pydantic import BaseModel

from ..auth import issue_user_token, verify_password
from ..config import get_settings
from ..db import get_session
from ..deps import current_user
from ..models import Device, GpsPoint, Route, Segment, User
from ..naming import split_route_id
from ..schemas import LoginRequest, TokenResponse
from ..storage import build_key, presign_get, presign_put

router = APIRouter(prefix="/api", tags=["ui"])

# kind -> the filename the device holds locally for that artifact.
_KIND_TO_DEVICE_FILE = {
  "qlog": "qlog.zst",
  "rlog": "rlog.zst",
  "qcamera": "qcamera.ts",
  "fcamera": "fcamera.hevc",
  "ecamera": "ecamera.hevc",
  "dcamera": "dcamera.hevc",
}
_CONTENT_TYPES = {".ts": "video/mp2t", ".hevc": "video/hevc", ".zst": "application/zstd"}


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, response: Response, session: AsyncSession = Depends(get_session)) -> TokenResponse:
  user = await session.scalar(select(User).where(User.email == body.email))
  if user is None or not verify_password(body.password, user.password_hash):
    raise HTTPException(status_code=401, detail="invalid credentials")
  token = issue_user_token(user.id, user.email)
  response.set_cookie("access_token", token, httponly=True, samesite="lax")
  return TokenResponse(access_token=token)


@router.get("/devices")
async def devices(session: AsyncSession = Depends(get_session), _: User = Depends(current_user)) -> list[dict]:
  rows = (await session.scalars(select(Device).order_by(Device.created_at))).all()
  return [
    {
      "dongle_id": d.dongle_id,
      "alias": d.alias,
      "is_online": d.is_online,
      "last_seen": d.last_seen.isoformat() if d.last_seen else None,
    }
    for d in rows
  ]


@router.get("/devices/{dongle_id}/routes")
async def device_routes(
  dongle_id: str, session: AsyncSession = Depends(get_session), _: User = Depends(current_user)
) -> list[dict]:
  rows = (
    await session.scalars(
      select(Route).where(Route.dongle_id == dongle_id).order_by(Route.start_time_utc.desc().nullslast())
    )
  ).all()
  return [
    {
      "fullname": r.id,
      "log_id": r.log_id,
      "start_time": r.start_time_utc.isoformat() if r.start_time_utc else None,
      "end_time": r.end_time_utc.isoformat() if r.end_time_utc else None,
      "segment_count": r.max_segment + 1,
      "length_m": r.length_m,
    }
    for r in rows
  ]


@router.get("/routes/{route_name}")
async def route_detail(
  route_name: str, session: AsyncSession = Depends(get_session), _: User = Depends(current_user)
) -> dict:
  route = await session.get(Route, route_name)
  if route is None:
    raise HTTPException(status_code=404, detail="unknown route")
  segs = (
    await session.scalars(select(Segment).where(Segment.route_id == route_name).order_by(Segment.segment_num))
  ).all()
  return {
    "fullname": route.id,
    "dongle_id": route.dongle_id,
    "start_time": route.start_time_utc.isoformat() if route.start_time_utc else None,
    "end_time": route.end_time_utc.isoformat() if route.end_time_utc else None,
    "length_m": route.length_m,
    "version": route.version,
    "git_commit": route.git_commit,
    "segments": [{"segment_num": s.segment_num, "proc_state": s.proc_state, "files": list((s.files or {}).keys())} for s in segs],
  }


@router.get("/routes/{route_name}/segments/{segment_num}/file/{kind}")
async def segment_file_url(
  route_name: str,
  segment_num: int,
  kind: str,
  session: AsyncSession = Depends(get_session),
  _: User = Depends(current_user),
) -> dict:
  seg = await session.scalar(
    select(Segment).where(Segment.route_id == route_name, Segment.segment_num == segment_num)
  )
  if seg is None or kind not in (seg.files or {}):
    raise HTTPException(status_code=404, detail="file not available")
  return {"url": presign_get(seg.files[kind]["key"])}


class RequestUploadBody(BaseModel):
  # which artifact kinds to pull from the device (default: full-res camera + rlog)
  kinds: list[str] = ["rlog", "fcamera", "ecamera", "qcamera"]
  segments: list[int] | None = None  # default: all segments 0..max_segment


@router.post("/routes/{route_name}/request_upload")
async def request_upload(
  route_name: str,
  body: RequestUploadBody,
  session: AsyncSession = Depends(get_session),
  _: User = Depends(current_user),
) -> dict:
  """Ask a live device (via Athena) to upload artifacts it hasn't sent yet.

  We presign a PUT for each requested file at our canonical storage key and send
  the device an `uploadFilesToUrls` JSON-RPC command; the device PUTs directly to
  storage and the upload lands exactly where the ingest worker expects it.
  """
  route = await session.get(Route, route_name)
  if route is None:
    raise HTTPException(status_code=404, detail="unknown route")
  split = split_route_id(route_name)
  if split is None:
    raise HTTPException(status_code=400, detail="bad route name")
  dongle_id, log_id = split

  segments = body.segments if body.segments is not None else list(range(max(route.max_segment, 0) + 1))
  files_data = []
  for seg in segments:
    for kind in body.kinds:
      filename = _KIND_TO_DEVICE_FILE.get(kind)
      if filename is None:
        continue
      device_fn = f"{log_id}--{seg}/{filename}"  # relative to the device's log root
      key = build_key(dongle_id, log_id, str(seg), filename)
      ct = next((c for ext, c in _CONTENT_TYPES.items() if filename.endswith(ext)), "application/octet-stream")
      url, headers = presign_put(key, content_type=ct)
      files_data.append({"fn": device_fn, "url": url, "headers": headers, "allow_cellular": False, "priority": 0})

  if not files_data:
    raise HTTPException(status_code=400, detail="no valid files requested")

  settings = get_settings()
  try:
    async with httpx.AsyncClient(timeout=35.0) as client:
      resp = await client.post(
        f"{settings.athena_internal_url}/devices/{dongle_id}/call",
        json={"method": "uploadFilesToUrls", "params": {"files_data": files_data}, "timeout": 30.0},
      )
    result = resp.json()
  except Exception as e:
    raise HTTPException(status_code=502, detail=f"athena unreachable: {e}") from e

  return {"requested": len(files_data), "athena": result}


@router.get("/routes/{route_name}/track")
async def route_track(
  route_name: str, session: AsyncSession = Depends(get_session), _: User = Depends(current_user)
) -> dict:
  pts = (
    await session.scalars(select(GpsPoint).where(GpsPoint.route_id == route_name).order_by(GpsPoint.t_utc))
  ).all()
  return {
    "route": route_name,
    "points": [[p.lat, p.lon] for p in pts],
    "speed": [p.speed for p in pts],
  }
