"""Auth unit tests — key handling, JWT verification, header parsing."""
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from app.auth import (
  AuthError,
  algorithm_for_key,
  cookie_jwt,
  derive_dongle_id,
  extract_bearer,
  hash_password,
  verify_password,
  verify_with_public_key,
)


def _rsa_pair():
  k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
  priv = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
  pub = k.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
  return priv, pub


def _ec_pair():
  k = ec.generate_private_key(ec.SECP256R1())
  priv = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
  pub = k.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
  return priv, pub


def test_derive_dongle_id_deterministic_and_16hex():
  _, pub = _rsa_pair()
  a = derive_dongle_id(pub)
  b = derive_dongle_id(pub + "\n")  # whitespace-normalized
  assert a == b
  assert len(a) == 16 and all(c in "0123456789abcdef" for c in a)


def test_algorithm_for_key():
  _, rsa_pub = _rsa_pair()
  _, ec_pub = _ec_pair()
  assert algorithm_for_key(rsa_pub) == "RS256"
  assert algorithm_for_key(ec_pub) == "ES256"


def test_verify_with_public_key_roundtrip_rsa():
  priv, pub = _rsa_pair()
  now = datetime.now(timezone.utc)
  token = jwt.encode({"identity": "abc", "exp": now + timedelta(hours=1)}, priv, algorithm="RS256")
  claims = verify_with_public_key(token, pub, "RS256")
  assert claims["identity"] == "abc"


def test_verify_register_requires_claim():
  priv, pub = _rsa_pair()
  token = jwt.encode({"register": True, "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, priv, algorithm="RS256")
  assert verify_with_public_key(token, pub, "RS256", require_register=True)["register"] is True
  bad = jwt.encode({"exp": datetime.now(timezone.utc) + timedelta(hours=1)}, priv, algorithm="RS256")
  with pytest.raises(AuthError):
    verify_with_public_key(bad, pub, "RS256", require_register=True)


def test_verify_rejects_wrong_key():
  priv, _ = _rsa_pair()
  _, other_pub = _rsa_pair()
  token = jwt.encode({"identity": "x", "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, priv, algorithm="RS256")
  with pytest.raises(AuthError):
    verify_with_public_key(token, other_pub, "RS256")


def test_extract_bearer():
  assert extract_bearer("JWT abc.def") == "abc.def"
  assert extract_bearer("Bearer xyz") == "xyz"
  with pytest.raises(AuthError):
    extract_bearer(None)
  with pytest.raises(AuthError):
    extract_bearer("Basic foo")


def test_cookie_jwt():
  assert cookie_jwt("jwt=tok; other=1") == "tok"
  assert cookie_jwt("other=1") is None
  assert cookie_jwt(None) is None


def test_password_hashing():
  h = hash_password("secret")
  assert verify_password("secret", h)
  assert not verify_password("wrong", h)
