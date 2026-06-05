"""Share-signature tests — used to authorize connect's qcamera HLS playback."""
from app.share import sign_share, verify_share


def test_sign_verify_roundtrip():
  exp, sig = sign_share("dead0000beef1111|0000007b--abc1234567")
  assert verify_share("dead0000beef1111|0000007b--abc1234567", exp, sig)


def test_verify_rejects_tampered_sig():
  exp, sig = sign_share("route-a")
  assert not verify_share("route-a", exp, sig[:-1] + ("0" if sig[-1] != "0" else "1"))


def test_verify_rejects_wrong_route():
  exp, sig = sign_share("route-a")
  assert not verify_share("route-b", exp, sig)


def test_verify_rejects_expired():
  # ttl in the past -> immediately expired
  exp, sig = sign_share("route-a", ttl=-10)
  assert not verify_share("route-a", exp, sig)


def test_verify_rejects_missing_or_malformed():
  assert not verify_share("route-a", None, None)
  assert not verify_share("route-a", "notanumber", "abc")
  assert not verify_share("route-a", "", "")
