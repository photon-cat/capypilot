"""Navigation endpoints — send destinations to the car and manage saved places.

Mirrors comma's `/v1/navigation/*` surface (see `@commaai/api` navigation.js):

  POST   /v1/navigation/{id}/set_destination   set/route a destination
  GET    /v1/navigation/{id}/next              device pulls a queued destination
  DELETE /v1/navigation/{id}/next              clear the queued destination
  GET    /v1/navigation/{id}/locations         list saved favorites/recents
  PUT    /v1/navigation/{id}/locations         add a saved location
  PATCH  /v1/navigation/{id}/locations         update a saved location
  DELETE /v1/navigation/{id}/locations         delete a saved location

When a destination is set and the device is online, we push it straight to the
car over Athena (`setNavDestination` JSON-RPC, which openpilot's athenad writes
to the `NavDestination` param). If the device is offline, we store it as the
single pending "next" destination, which the device pulls on reconnect.
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..deps import authenticated_device, current_user
from ..models import Device, NavDestination, NavLocation, User

router = APIRouter(tags=["navigation"])
log = logging.getLogger("capypilot.navigation")


class DestinationBody(BaseModel):
  latitude: float
  longitude: float
  place_name: str
  place_details: str | None = None


class SavedLocationBody(BaseModel):
  latitude: float
  longitude: float
  place_name: str
  place_details: str | None = None
  save_type: str = "favorite"  # favorite|recent|home|work
  label: str | None = None


class UpdateLocationBody(BaseModel):
  id: int
  save_type: str | None = None
  label: str | None = None


class DeleteLocationBody(BaseModel):
  id: int


def _destination_dict(d: NavDestination) -> dict:
  return {
    "place_name": d.place_name,
    "place_details": d.place_details,
    "latitude": d.latitude,
    "longitude": d.longitude,
  }


def _location_dict(loc: NavLocation) -> dict:
  return {
    "id": loc.id,
    "dongle_id": loc.dongle_id,
    "save_type": loc.save_type,
    "label": loc.label,
    "place_name": loc.place_name,
    "place_details": loc.place_details,
    "latitude": loc.latitude,
    "longitude": loc.longitude,
    "modified": loc.modified.isoformat() if loc.modified else None,
  }


async def _push_to_device(dongle_id: str, body: DestinationBody) -> bool:
  """Try to push the destination to a live device via Athena. True if delivered."""
  settings = get_settings()
  try:
    async with httpx.AsyncClient(timeout=20.0) as client:
      resp = await client.post(
        f"{settings.athena_internal_url}/devices/{dongle_id}/call",
        json={
          "method": "setNavDestination",
          "params": {
            "latitude": body.latitude,
            "longitude": body.longitude,
            "place_name": body.place_name,
            "place_details": body.place_details or "",
          },
          "timeout": 15.0,
        },
      )
    data = resp.json()
    return bool(data.get("online")) and "result" in data
  except Exception:
    log.exception("athena push failed for %s", dongle_id)
    return False


@router.post("/v1/navigation/{dongle_id}/set_destination")
async def set_destination(
  body: DestinationBody,
  dongle_id: str = Path(...),
  session: AsyncSession = Depends(get_session),
  _: User = Depends(current_user),
) -> dict:
  device = await session.get(Device, dongle_id)
  if device is None:
    raise HTTPException(status_code=404, detail="unknown device")

  delivered = await _push_to_device(dongle_id, body)
  if not delivered:
    # Store as the single pending "next" destination for the device to pull.
    dest = await session.get(NavDestination, dongle_id)
    if dest is None:
      dest = NavDestination(dongle_id=dongle_id)
      session.add(dest)
    dest.place_name = body.place_name
    dest.place_details = body.place_details
    dest.latitude = body.latitude
    dest.longitude = body.longitude

  # Record it as a recent destination (best-effort history).
  session.add(
    NavLocation(
      dongle_id=dongle_id,
      save_type="recent",
      place_name=body.place_name,
      place_details=body.place_details,
      latitude=body.latitude,
      longitude=body.longitude,
    )
  )
  return {"success": True, "saved_next": not delivered}


@router.get("/v1/navigation/{dongle_id}/next")
async def navigation_next(
  device: Device = Depends(authenticated_device),
  session: AsyncSession = Depends(get_session),
) -> dict | None:
  """Device pulls (and consumes) its queued destination. `null` if none."""
  dest = await session.get(NavDestination, device.dongle_id)
  if dest is None:
    return None
  out = _destination_dict(dest)
  await session.delete(dest)  # next is one-shot: deleted after read
  return out


@router.delete("/v1/navigation/{dongle_id}/next")
async def clear_navigation_next(
  dongle_id: str = Path(...),
  session: AsyncSession = Depends(get_session),
  _: User = Depends(current_user),
) -> dict:
  dest = await session.get(NavDestination, dongle_id)
  deleted = _destination_dict(dest) if dest else None
  if dest is not None:
    await session.delete(dest)
  return {"success": True, "deleted": deleted}


@router.get("/v1/navigation/{dongle_id}/locations")
async def list_locations(
  dongle_id: str = Path(...),
  session: AsyncSession = Depends(get_session),
  _: User = Depends(current_user),
) -> list[dict]:
  rows = (
    await session.scalars(
      select(NavLocation).where(NavLocation.dongle_id == dongle_id).order_by(NavLocation.modified.desc())
    )
  ).all()
  return [_location_dict(loc) for loc in rows]


@router.put("/v1/navigation/{dongle_id}/locations")
async def save_location(
  body: SavedLocationBody,
  dongle_id: str = Path(...),
  session: AsyncSession = Depends(get_session),
  _: User = Depends(current_user),
) -> dict:
  session.add(
    NavLocation(
      dongle_id=dongle_id,
      save_type=body.save_type,
      label=body.label,
      place_name=body.place_name,
      place_details=body.place_details,
      latitude=body.latitude,
      longitude=body.longitude,
    )
  )
  return {"success": True}


@router.patch("/v1/navigation/{dongle_id}/locations")
async def update_location(
  body: UpdateLocationBody,
  dongle_id: str = Path(...),
  session: AsyncSession = Depends(get_session),
  _: User = Depends(current_user),
) -> dict:
  loc = await session.get(NavLocation, body.id)
  if loc is None or loc.dongle_id != dongle_id:
    raise HTTPException(status_code=404, detail="unknown location")
  if body.save_type is not None:
    loc.save_type = body.save_type
  if body.label is not None:
    loc.label = body.label
  return {"success": True}


@router.delete("/v1/navigation/{dongle_id}/locations")
async def delete_location(
  body: DeleteLocationBody,
  dongle_id: str = Path(...),
  session: AsyncSession = Depends(get_session),
  _: User = Depends(current_user),
) -> dict:
  await session.execute(
    delete(NavLocation).where(NavLocation.id == body.id, NavLocation.dongle_id == dongle_id)
  )
  return {"success": True}
