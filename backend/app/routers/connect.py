"""commaai/connect compatibility layer.

These endpoints reproduce the exact API contract the official open-source
**connect** web app (github.com/commaai/connect, via the `@commaai/api` lib)
expects from `api.comma.ai`, so connect can be pointed at this backend as a
drop-in frontend — only its `public/config.js` base URLs need changing.

Contract notes that drive the shapes here:
  - Auth is `Authorization: JWT <token>` (handled by `current_user`); the token
    is minted by `POST /v2/auth/`.
  - The drive list comes from `routes_segments`; connect builds the whole route
    detail view from that one response (it does not call `/v1/route/{r}/`).
  - Video is HLS: connect feeds `/v1/route/{fullname}/qcamera.m3u8?exp=&sig=`
    to hls.js, which does NOT send our auth header — so that endpoint authorizes
    via the `exp`/`sig` query params (see app/share.py) instead.
  - The map path + timeline come from `{route.url}/{i}/coords.json` and
    `events.json`, fetched by connect WITHOUT an auth header — so those are
    served open (acceptable for a self-hosted backend; documented in README).
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import issue_user_token, verify_password
from ..config import get_settings
from ..db import get_session
from ..deps import current_user
from ..models import Device, GpsPoint, Route, Segment, User
from ..naming import route_id
from ..share import sign_share, verify_share
from ..storage import presign_get

router = APIRouter(tags=["connect"])

_MILES_PER_METER = 1.0 / 1609.344


# ── auth ─────────────────────────────────────────────────────────────────────
@router.post("/v2/auth/")
@router.post("/v2/auth")
async def v2_auth(
  provider: str = Form(...),
  code: str = Form(...),
  session: AsyncSession = Depends(get_session),
) -> dict:
  """Token exchange used by connect's login.

  comma's flow is OAuth -> `{code, provider}` -> `{access_token}`. Self-hosting
  the OAuth providers is out of scope (their redirect URIs are hardcoded to
  comma), so we support a `provider=email` mode where `code` is `email:password`
  against the local user table. This yields the same `{access_token}` connect
  stores and then sends as `Authorization: JWT <token>`.
  """
  if provider == "email":
    email, _, password = code.partition(":")
    user = await session.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(password, user.password_hash):
      raise HTTPException(status_code=401, detail="invalid credentials")
    return {"access_token": issue_user_token(user.id, user.email)}
  raise HTTPException(status_code=400, detail=f"unsupported provider '{provider}'")


@router.get("/v1/me/")
@router.get("/v1/me")
async def connect_me(user: User = Depends(current_user)) -> dict:
  return {
    "id": str(user.id),
    "user_id": str(user.id),
    "email": user.email,
    "username": user.email,
    "superuser": True,
    "points": 0,
  }


# ── devices ──────────────────────────────────────────────────────────────────
def _device_dict(d: Device, now: float) -> dict:
  """Device object in connect's shape (see @commaai/api devices.js consumers)."""
  last_ping = int(now) if d.is_online else (int(d.last_seen.timestamp()) if d.last_seen else 0)
  return {
    "dongle_id": d.dongle_id,
    "alias": d.alias,
    "serial": d.serial,
    "device_type": d.device_type or "threex",
    "is_owner": True,
    "prime": False,
    "prime_type": 0,
    "shared": False,
    "last_athena_ping": last_ping,
    "openpilot_version": d.openpilot_version,
    "network_metered": False,
    "sim_id": None,
    "trial_claimed": False,
    "eligible_features": {"prime": False, "prime_data": False, "nav": False},
  }


@router.get("/v1/me/devices/")
@router.get("/v1/me/devices")
async def connect_my_devices(
  session: AsyncSession = Depends(get_session), _: User = Depends(current_user)
) -> list[dict]:
  rows = (await session.scalars(select(Device).order_by(Device.created_at))).all()
  now = time.time()
  return [_device_dict(d, now) for d in rows]


@router.get("/v1.1/devices/{dongle_id}/")
@router.get("/v1.1/devices/{dongle_id}")
async def connect_device(
  dongle_id: str, session: AsyncSession = Depends(get_session), _: User = Depends(current_user)
) -> dict:
  d = await session.get(Device, dongle_id)
  if d is None:
    raise HTTPException(status_code=404, detail="unknown device")
  return _device_dict(d, time.time())


@router.patch("/v1/devices/{dongle_id}/")
@router.patch("/v1/devices/{dongle_id}")
async def connect_patch_device(
  dongle_id: str,
  body: dict,
  session: AsyncSession = Depends(get_session),
  _: User = Depends(current_user),
) -> dict:
  d = await session.get(Device, dongle_id)
  if d is None:
    raise HTTPException(status_code=404, detail="unknown device")
  if "alias" in body:
    d.alias = body["alias"]
  return _device_dict(d, time.time())


@router.get("/v1.1/devices/{dongle_id}/stats")
async def connect_device_stats(
  dongle_id: str, session: AsyncSession = Depends(get_session), _: User = Depends(current_user)
) -> dict:
  routes = (await session.scalars(select(Route).where(Route.dongle_id == dongle_id))).all()
  week_cutoff = datetime.now(timezone.utc) - timedelta(days=7)

  def _agg(rs: list[Route]) -> dict:
    distance = sum((r.length_m or 0.0) for r in rs) * _MILES_PER_METER
    minutes = 0.0
    for r in rs:
      if r.start_time_utc and r.end_time_utc:
        minutes += max(0.0, (r.end_time_utc - r.start_time_utc).total_seconds() / 60.0)
    return {"distance": distance, "routes": len(rs), "minutes": minutes}

  week = [r for r in routes if r.start_time_utc and r.start_time_utc >= week_cutoff]
  return {"all": _agg(routes), "week": _agg(week)}


@router.get("/v1/devices/{dongle_id}/location")
async def connect_device_location(
  dongle_id: str, session: AsyncSession = Depends(get_session), _: User = Depends(current_user)
) -> dict:
  pt = await session.scalar(
    select(GpsPoint)
    .join(Route, Route.id == GpsPoint.route_id)
    .where(Route.dongle_id == dongle_id)
    .order_by(GpsPoint.t_utc.desc())
    .limit(1)
  )
  if pt is None:
    return {"error": "no_segments_uploaded"}
  return {"lat": pt.lat, "lng": pt.lon, "time": int(pt.t_utc.timestamp())}


# ── routes_segments (the drive list — the most important endpoint) ───────────
def _segment_times(route: Route, segs: list[Segment], pts: list[GpsPoint]) -> tuple[list[int], list[int]]:
  """Per-segment [start, end] in ms epoch, ordered to match `segment_numbers`.

  Prefer the GPS time span actually recorded in each segment; fall back to the
  route start plus ~60s per segment index when a segment has no GPS fix.
  """
  by_seg: dict[int, list[datetime]] = defaultdict(list)
  for p in pts:
    by_seg[p.segment_num].append(p.t_utc)
  base = route.start_time_utc
  starts: list[int] = []
  ends: list[int] = []
  for s in segs:
    times = by_seg.get(s.segment_num)
    if times:
      starts.append(int(min(times).timestamp() * 1000))
      ends.append(int(max(times).timestamp() * 1000))
    elif base is not None:
      st = base + timedelta(seconds=60 * s.segment_num)
      starts.append(int(st.timestamp() * 1000))
      ends.append(int((st + timedelta(seconds=60)).timestamp() * 1000))
    else:
      starts.append(0)
      ends.append(0)
  return starts, ends


async def _route_segment_dict(session: AsyncSession, r: Route) -> dict:
  segs = (
    await session.scalars(select(Segment).where(Segment.route_id == r.id).order_by(Segment.segment_num))
  ).all()
  pts = (
    await session.scalars(select(GpsPoint).where(GpsPoint.route_id == r.id).order_by(GpsPoint.t_utc))
  ).all()

  seg_starts, seg_ends = _segment_times(r, segs, pts)
  start_ms = int(r.start_time_utc.timestamp() * 1000) if r.start_time_utc else (seg_starts[0] if seg_starts else 0)
  end_ms = int(r.end_time_utc.timestamp() * 1000) if r.end_time_utc else (seg_ends[-1] if seg_ends else 0)
  miles = (r.length_m or 0.0) * _MILES_PER_METER
  exp, sig = sign_share(r.id)
  api_host = get_settings().api_host.rstrip("/")

  start_lat = pts[0].lat if pts else None
  start_lng = pts[0].lon if pts else None
  end_lat = pts[-1].lat if pts else None
  end_lng = pts[-1].lon if pts else None

  return {
    "fullname": r.id,
    "dongle_id": r.dongle_id,
    "log_id": r.log_id,
    # base URL connect fetches `{url}/{i}/coords.json` and `events.json` from.
    "url": f"{api_host}/connect/files/{r.dongle_id}/{r.log_id}",
    "segment_start_times": seg_starts,
    "segment_end_times": seg_ends,
    "segment_numbers": [s.segment_num for s in segs],
    "start_time_utc_millis": start_ms,
    "end_time_utc_millis": end_ms,
    "create_time": int(r.created_at.timestamp()) if r.created_at else 0,
    "distance": miles,
    "length": miles,
    "maxqlog": r.max_segment,
    "maxlog": r.max_segment,
    "start_lat": start_lat,
    "start_lng": start_lng,
    "end_lat": end_lat,
    "end_lng": end_lng,
    "share_exp": exp,
    "share_sig": sig,
    "version": r.version,
    "git_commit": r.git_commit,
    "platform": None,
    "is_public": False,
    "is_preserved": False,
  }


@router.get("/v1/devices/{dongle_id}/routes_segments")
async def connect_routes_segments(
  dongle_id: str,
  start: int | None = Query(default=None),
  end: int | None = Query(default=None),
  limit: int | None = Query(default=None),
  route_str: str | None = Query(default=None),
  session: AsyncSession = Depends(get_session),
  _: User = Depends(current_user),
) -> list[dict]:
  q = select(Route).where(Route.dongle_id == dongle_id)
  if route_str:
    q = q.where(Route.id == route_str)
  if start is not None:
    q = q.where(Route.start_time_utc >= datetime.fromtimestamp(start / 1000.0, tz=timezone.utc))
  if end is not None:
    q = q.where(Route.start_time_utc <= datetime.fromtimestamp(end / 1000.0, tz=timezone.utc))
  q = q.order_by(Route.start_time_utc.desc().nullslast())
  if limit is not None:
    q = q.limit(limit)
  routes = (await session.scalars(q)).all()
  return [await _route_segment_dict(session, r) for r in routes]


# ── per-segment map data (open: connect fetches these without an auth header) ─
@router.get("/connect/files/{dongle_id}/{log_id}/{segment_num}/coords.json")
async def connect_coords(
  dongle_id: str, log_id: str, segment_num: int, session: AsyncSession = Depends(get_session)
) -> list[dict]:
  rid = route_id(dongle_id, log_id)
  route = await session.get(Route, rid)
  base = route.start_time_utc if route else None
  pts = (
    await session.scalars(
      select(GpsPoint)
      .where(GpsPoint.route_id == rid, GpsPoint.segment_num == segment_num)
      .order_by(GpsPoint.t_utc)
    )
  ).all()
  out = []
  for p in pts:
    t = int((p.t_utc - base).total_seconds() * 1000) if base else int(p.t_utc.timestamp() * 1000)
    out.append({"t": t, "lng": p.lon, "lat": p.lat})
  return out


@router.get("/connect/files/{dongle_id}/{log_id}/{segment_num}/events.json")
async def connect_events(dongle_id: str, log_id: str, segment_num: int) -> list[dict]:
  # We don't extract timeline events from logs yet; connect tolerates an empty
  # list (video simply starts at offset 0).
  return []


# ── qcamera HLS (authorized by signed exp/sig, NOT the JWT header) ───────────
def _seg_duration_s(seg: Segment) -> float:
  if seg.start_mono is not None and seg.end_mono is not None and seg.end_mono > seg.start_mono:
    return (seg.end_mono - seg.start_mono) / 1e9
  return 60.0


@router.get("/v1/route/{fullname}/qcamera.m3u8")
async def connect_qcamera_m3u8(
  fullname: str,
  exp: str | None = Query(default=None),
  sig: str | None = Query(default=None),
  session: AsyncSession = Depends(get_session),
) -> Response:
  if not verify_share(fullname, exp, sig):
    raise HTTPException(status_code=403, detail="invalid or expired share signature")
  route = await session.get(Route, fullname)
  if route is None:
    raise HTTPException(status_code=404, detail="unknown route")
  segs = (
    await session.scalars(select(Segment).where(Segment.route_id == fullname).order_by(Segment.segment_num))
  ).all()

  max_dur = 1
  body_lines: list[str] = []
  for s in segs:
    meta = (s.files or {}).get("qcamera")
    if not meta or not meta.get("key"):
      continue
    dur = _seg_duration_s(s)
    max_dur = max(max_dur, int(dur) + 1)
    body_lines.append(f"#EXTINF:{dur:.3f},")
    body_lines.append(presign_get(meta["key"]))

  if not body_lines:
    raise HTTPException(status_code=404, detail="no qcamera segments for route")

  playlist = [
    "#EXTM3U",
    "#EXT-X-VERSION:3",
    "#EXT-X-PLAYLIST-TYPE:VOD",
    f"#EXT-X-TARGETDURATION:{max_dur}",
    "#EXT-X-MEDIA-SEQUENCE:0",
    *body_lines,
    "#EXT-X-ENDLIST",
  ]
  return Response("\n".join(playlist) + "\n", media_type="application/vnd.apple.mpegurl")
