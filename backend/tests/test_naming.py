"""Naming / path-parsing tests — must match openpilot's route/segment scheme."""
from app.naming import file_kind, parse_upload_path, route_id, split_route_id
from app.storage import build_key
from app.ingest.worker import _split_key


def test_parse_upload_path_v2():
  up = parse_upload_path("0000007b--abc1234567--3/qlog.zst")
  assert up is not None
  assert up.log_id == "0000007b--abc1234567"
  assert up.segment_num == 3
  assert up.filename == "qlog.zst"
  assert up.segment_dir == "0000007b--abc1234567--3"


def test_parse_upload_path_legacy_timestamp():
  up = parse_upload_path("2024-05-30--14-25-18--0/rlog.zst")
  assert up is not None
  assert up.log_id == "2024-05-30--14-25-18"
  assert up.segment_num == 0


def test_parse_upload_path_rejects_non_segment():
  assert parse_upload_path("boot/somelog") is None
  assert parse_upload_path("qlog.zst") is None


def test_build_key_and_split_roundtrip():
  key = build_key("1234567890abcdef", "0000007b--abc1234567", "3", "qlog.zst")
  assert key == "1234567890abcdef/0000007b--abc1234567/3/qlog.zst"
  dongle, log_id, seg, fn = _split_key(key)
  assert (dongle, log_id, seg, fn) == ("1234567890abcdef", "0000007b--abc1234567", 3, "qlog.zst")


def test_route_id_and_split():
  rid = route_id("1234567890abcdef", "0000007b--abc1234567")
  assert rid == "1234567890abcdef|0000007b--abc1234567"
  assert split_route_id(rid) == ("1234567890abcdef", "0000007b--abc1234567")


def test_file_kind():
  assert file_kind("qlog.zst") == "qlog"
  assert file_kind("qlog.bz2") == "qlog"
  assert file_kind("rlog.zst") == "rlog"
  assert file_kind("qcamera.ts") == "qcamera"
  assert file_kind("fcamera.hevc") == "fcamera"
  assert file_kind("ecamera.hevc") == "ecamera"
  assert file_kind("dcamera.hevc") == "dcamera"
  assert file_kind("random.bin") is None
