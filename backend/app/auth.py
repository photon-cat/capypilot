"""Authentication: device JWTs (verified against registered public keys) and
user JWTs (for the web UI, signed with our own secret).

Device auth mirrors `common/api.py`: the device signs a JWT with its private
key (`/persist/comma/id_{rsa,ecdsa}`) using RS256 (RSA) or ES256 (ECDSA), with
payload `{identity: dongle_id, ...}`. We hold the matching public key from
registration, so we verify signatures without any shared secret.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from .config import get_settings

# algorithm by openpilot key name (see common/api.py KEYS)
RSA_ALGS = ("RS256",)
EC_ALGS = ("ES256",)
DEVICE_ALGS = list(RSA_ALGS + EC_ALGS)


class AuthError(Exception):
  pass


def derive_dongle_id(public_key_pem: str) -> str:
  """Deterministic, idempotent dongle_id: first 16 hex of sha256(normalized key).

  Deterministic derivation means registration is idempotent and needs no central
  ID allocator; documented as easily swappable for random+unique allocation.
  """
  normalized = public_key_pem.strip().encode()
  return hashlib.sha256(normalized).hexdigest()[:16]


def algorithm_for_key(public_key_pem: str) -> str:
  """Pick the JWT algorithm the device will use, from the public key type."""
  key = load_pem_public_key(public_key_pem.strip().encode())
  # RSAPublicKey has `key_size`; EC keys have `curve`.
  if hasattr(key, "curve"):
    return "ES256"
  return "RS256"


def verify_with_public_key(token: str, public_key_pem: str, algorithm: str, *, require_register: bool = False) -> dict:
  """Verify `token`'s signature against `public_key_pem`. Returns the claims."""
  try:
    claims = jwt.decode(
      token,
      public_key_pem,
      algorithms=[algorithm],
      options={"verify_aud": False, "require": []},
    )
  except jwt.PyJWTError as e:
    raise AuthError(f"invalid token: {e}") from e
  if require_register and not claims.get("register"):
    raise AuthError("register token missing 'register' claim")
  return claims


def extract_bearer(authorization: str | None) -> str:
  """Pull the raw token from an `Authorization: JWT <token>` header.

  The device uses the `JWT` scheme (common/api.py:48); accept `Bearer` too.
  """
  if not authorization:
    raise AuthError("missing Authorization header")
  parts = authorization.split(None, 1)
  if len(parts) != 2 or parts[0].lower() not in ("jwt", "bearer"):
    raise AuthError("malformed Authorization header")
  return parts[1].strip()


def cookie_jwt(cookie_header: str | None) -> str | None:
  """Extract the `jwt` cookie value used by the Athena WebSocket handshake."""
  if not cookie_header:
    return None
  for part in cookie_header.split(";"):
    name, _, value = part.strip().partition("=")
    if name == "jwt":
      return value
  return None


# ── user (web UI) tokens ─────────────────────────────────────────────────────
def issue_user_token(user_id: int, email: str) -> str:
  s = get_settings()
  now = datetime.now(timezone.utc)
  payload = {"sub": str(user_id), "email": email, "iat": now, "exp": now + timedelta(seconds=s.user_jwt_ttl)}
  return jwt.encode(payload, s.user_jwt_secret, algorithm="HS256")


def verify_user_token(token: str) -> dict:
  s = get_settings()
  try:
    return jwt.decode(token, s.user_jwt_secret, algorithms=["HS256"])
  except jwt.PyJWTError as e:
    raise AuthError(f"invalid user token: {e}") from e


def hash_password(password: str) -> str:
  # PBKDF2; salt embedded. Adequate for a self-hosted single-admin UI.
  salt = hashlib.sha256(get_settings().user_jwt_secret.encode()).digest()[:16]
  dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
  return dk.hex()


def verify_password(password: str, password_hash: str) -> bool:
  return hash_password(password) == password_hash
