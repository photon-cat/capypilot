"""Shared FastAPI dependencies for auth."""
from __future__ import annotations

from fastapi import Cookie, Depends, Header, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import AuthError, extract_bearer, verify_user_token, verify_with_public_key
from .db import get_session
from .models import Device, User


async def authenticated_device(
  dongle_id: str = Path(...),
  authorization: str | None = Header(default=None),
  session: AsyncSession = Depends(get_session),
) -> Device:
  """Resolve + verify the device named in the path against its registered key."""
  device = await session.get(Device, dongle_id)
  if device is None:
    raise HTTPException(status_code=404, detail="unknown device")
  try:
    token = extract_bearer(authorization)
    claims = verify_with_public_key(token, device.public_key, device.jwt_algorithm)
  except AuthError as e:
    raise HTTPException(status_code=401, detail=str(e)) from e
  if claims.get("identity") != dongle_id:
    raise HTTPException(status_code=403, detail="token identity mismatch")
  return device


async def current_user(
  authorization: str | None = Header(default=None),
  access_token: str | None = Cookie(default=None),
  session: AsyncSession = Depends(get_session),
) -> User:
  """Resolve the web-UI user from a bearer header or the `access_token` cookie."""
  token: str | None = None
  if authorization:
    try:
      token = extract_bearer(authorization)
    except AuthError:
      token = None
  token = token or access_token
  if not token:
    raise HTTPException(status_code=401, detail="not authenticated")
  try:
    claims = verify_user_token(token)
  except AuthError as e:
    raise HTTPException(status_code=401, detail=str(e)) from e
  user = await session.get(User, int(claims["sub"]))
  if user is None:
    raise HTTPException(status_code=401, detail="unknown user")
  return user
