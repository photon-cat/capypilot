"""Device registration — POST /v2/pilotauth/.

Mirrors `system/athena/registration.py:76`. The device self-signs a
`register_token` JWT (`{register: true}`) with its private key and sends the
matching `public_key`. We verify the signature against that public key (proof of
key possession), derive a stable dongle_id, and upsert the device.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
  AuthError,
  algorithm_for_key,
  derive_dongle_id,
  verify_with_public_key,
)
from ..db import get_session
from ..models import Device
from ..schemas import PilotAuthResponse

router = APIRouter(tags=["device"])
log = logging.getLogger("capypilot.pilotauth")


@router.post("/v2/pilotauth/", response_model=PilotAuthResponse)
@router.post("/v2/pilotauth", response_model=PilotAuthResponse)
async def pilotauth(
  public_key: str = Query(...),
  register_token: str = Query(...),
  imei: str | None = Query(default=None),
  imei2: str | None = Query(default=None),
  serial: str | None = Query(default=None),
  session: AsyncSession = Depends(get_session),
) -> PilotAuthResponse:
  public_key = public_key.strip()
  if not public_key:
    raise HTTPException(status_code=400, detail="missing public_key")

  try:
    algorithm = algorithm_for_key(public_key)
  except Exception as e:
    raise HTTPException(status_code=400, detail=f"invalid public_key: {e}") from e

  # Prove the caller holds the private key matching the supplied public key.
  try:
    verify_with_public_key(register_token, public_key, algorithm, require_register=True)
  except AuthError as e:
    raise HTTPException(status_code=403, detail=str(e)) from e

  dongle_id = derive_dongle_id(public_key)

  device = await session.get(Device, dongle_id)
  if device is None:
    device = Device(
      dongle_id=dongle_id,
      public_key=public_key,
      jwt_algorithm=algorithm,
      serial=serial,
      imei=imei,
      imei2=imei2,
    )
    session.add(device)
    log.info("registered new device %s (serial=%s)", dongle_id, serial)
  else:
    # Idempotent re-registration: refresh identifiers, keep the key.
    device.public_key = public_key
    device.jwt_algorithm = algorithm
    device.serial = serial or device.serial
    device.imei = imei or device.imei
    device.imei2 = imei2 or device.imei2

  return PilotAuthResponse(dongle_id=dongle_id)
