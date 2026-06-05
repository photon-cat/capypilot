"""Share-link signing for connect-compatible video playback.

comma's connect plays a route's qcamera as HLS: it requests
`/v1/route/{fullname}/qcamera.m3u8?exp=<unix>&sig=<hmac>` where `exp`/`sig` come
from the `share_exp`/`share_sig` fields embedded in each `routes_segments` entry.

The browser's HLS player (hls.js) issues that request WITHOUT our
`Authorization: JWT` header, so the m3u8 endpoint can't rely on the normal user
JWT — it authorizes via these signed query params instead. We sign with the
same secret used for user JWTs; a signature is just `HMAC-SHA256(secret,
"{fullname}:{exp}")`, compared in constant time.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

from .config import get_settings


def _digest(fullname: str, exp: int) -> str:
  secret = get_settings().user_jwt_secret.encode()
  msg = f"{fullname}:{exp}".encode()
  return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def sign_share(fullname: str, ttl: int | None = None) -> tuple[str, str]:
  """Return (exp, sig) for a route share link. `exp` is a unix-seconds string."""
  s = get_settings()
  exp = int(datetime.now(timezone.utc).timestamp()) + (ttl if ttl is not None else s.download_url_ttl)
  return str(exp), _digest(fullname, exp)


def verify_share(fullname: str, exp: str | None, sig: str | None) -> bool:
  """Validate an exp/sig pair: well-formed, unexpired, and signature matches."""
  if not exp or not sig:
    return False
  try:
    exp_int = int(exp)
  except (TypeError, ValueError):
    return False
  if exp_int < int(datetime.now(timezone.utc).timestamp()):
    return False
  return hmac.compare_digest(_digest(fullname, exp_int), sig)
