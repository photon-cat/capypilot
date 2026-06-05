"""Ingest worker: turn a stored object into indexed rows.

Triggered when an object lands in storage (MinIO bucket notification ->
/internal/s3-event). Two responsibilities:

  1. Mark the file present on its segment (every artifact kind).
  2. If it's a qlog, parse it and upsert route/segment metadata + GPS trace.

Idempotent: re-processing the same key overwrites cleanly.
"""
from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import Device, GpsPoint, Route, Segment
from ..naming import file_kind, route_id
from ..storage import get_bytes, head_object

log = logging.getLogger("capypilot.ingest")


def _split_key(key: str) -> tuple[str, str, int, str] | None:
  """Reverse build_key: '{dongle}/{log_id}/{seg}/{file}' -> parts."""
  parts = key.split("/")
  if len(parts) < 4:
    return None
  dongle_id = parts[0]
  filename = parts[-1]
  segment_str = parts[-2]
  log_id = "/".join(parts[1:-2])  # log_id never contains '/', but be defensive
  if not segment_str.isdigit():
    return None
  return dongle_id, log_id, int(segment_str), filename


async def _get_or_create_segment(session: AsyncSession, rid: str, segment_num: int) -> Segment:
  seg = await session.scalar(
    select(Segment).where(Segment.route_id == rid, Segment.segment_num == segment_num)
  )
  if seg is None:
    seg = Segment(route_id=rid, segment_num=segment_num, files={})
    session.add(seg)
    await session.flush()
  return seg


async def _ensure_route(session: AsyncSession, rid: str, dongle_id: str, log_id: str) -> Route:
  route = await session.get(Route, rid)
  if route is None:
    route = Route(id=rid, dongle_id=dongle_id, log_id=log_id)
    session.add(route)
    await session.flush()
  return route


async def process_object(session: AsyncSession, key: str) -> None:
  parsed = _split_key(key)
  if parsed is None:
    log.info("ingest skip (unindexable key) %s", key)
    return
  dongle_id, log_id, segment_num, filename = parsed
  kind = file_kind(filename)
  if kind is None:
    log.info("ingest skip (unknown file kind) %s", key)
    return

  rid = route_id(dongle_id, log_id)
  await _ensure_route(session, rid, dongle_id, log_id)
  seg = await _get_or_create_segment(session, rid, segment_num)

  # 1) mark file presence
  info = await head_object(key)
  files = dict(seg.files or {})
  files[kind] = {"key": key, "size": info.size if info else None, "etag": info.etag if info else None}
  seg.files = files

  # 2) parse qlog for metadata + GPS
  if kind == "qlog":
    await _index_qlog(session, route_id=rid, segment_num=segment_num, key=key)

  # keep route.max_segment current
  route = await session.get(Route, rid)
  if route is not None and segment_num > route.max_segment:
    route.max_segment = segment_num

  await session.commit()
  log.info("ingest ok %s (kind=%s)", key, kind)


async def _index_qlog(session: AsyncSession, *, route_id: str, segment_num: int, key: str) -> None:
  from .parser import parse_segment  # lazy: only pulls in capnp here

  blob = await get_bytes(key)
  s = get_settings()
  parsed = parse_segment(blob, decimate_to=s.gps_points_per_segment)

  seg = await _get_or_create_segment(session, route_id, segment_num)
  seg.proc_state = "parsed"
  seg.start_mono = parsed.start_mono
  seg.end_mono = parsed.end_mono

  route = await session.get(Route, route_id)
  if route is not None:
    if parsed.version:
      route.version = parsed.version
      # surface the latest seen openpilot version on the device (connect shows it)
      device = await session.get(Device, route.dongle_id)
      if device is not None:
        device.openpilot_version = parsed.version
    if parsed.git_commit:
      route.git_commit = parsed.git_commit
    if parsed.init_params:
      route.init_params = parsed.init_params
    # route.start_time = earliest segment's wall time
    if parsed.wall_time_utc and (route.start_time_utc is None or segment_num == 0):
      route.start_time_utc = parsed.wall_time_utc

  # replace GPS points for this segment (idempotent re-parse)
  await session.execute(
    delete(GpsPoint).where(GpsPoint.route_id == route_id, GpsPoint.segment_num == segment_num)
  )
  for g in parsed.gps:
    session.add(
      GpsPoint(
        route_id=route_id,
        segment_num=segment_num,
        t_utc=g.t_utc,
        lat=g.lat,
        lon=g.lon,
        alt=g.alt,
        speed=g.speed,
        bearing=g.bearing,
      )
    )

  # recompute route end time + length from all points (cheap; segments are small)
  await _recompute_route_aggregates(session, route_id)


async def _recompute_route_aggregates(session: AsyncSession, route_id: str) -> None:
  pts = (
    await session.scalars(
      select(GpsPoint).where(GpsPoint.route_id == route_id).order_by(GpsPoint.t_utc)
    )
  ).all()
  route = await session.get(Route, route_id)
  if route is None or not pts:
    return
  route.end_time_utc = pts[-1].t_utc
  if route.start_time_utc is None:
    route.start_time_utc = pts[0].t_utc
  route.length_m = _haversine_length([(p.lat, p.lon) for p in pts])


def _haversine_length(coords: list[tuple[float, float]]) -> float:
  import math

  total = 0.0
  R = 6371000.0
  for (lat1, lon1), (lat2, lon2) in zip(coords, coords[1:]):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    total += 2 * R * math.asin(min(1.0, math.sqrt(a)))
  return total
