"""Unit tests for the connect-compat shaping helpers (no DB required).

These verify the exact response shapes connect's `@commaai/api` consumers
depend on: device objects, per-segment time arrays, and HLS segment durations.
"""
from datetime import datetime, timezone

from app.models import Device, GpsPoint, Route, Segment
from app.routers.connect import _device_dict, _seg_duration_s, _segment_times


def _dt(s: int) -> datetime:
  return datetime.fromtimestamp(s, tz=timezone.utc)


def test_device_dict_shape_and_online():
  d = Device(dongle_id="dead0000beef1111", alias="car", serial="CAPY01",
             device_type="threex", openpilot_version="0.9.9", is_online=True, last_seen=_dt(1000))
  out = _device_dict(d, now=5000.0)
  # required keys connect reads
  for k in ("dongle_id", "alias", "device_type", "is_owner", "prime", "shared",
            "last_athena_ping", "openpilot_version", "network_metered"):
    assert k in out
  assert out["is_owner"] is True
  # online -> last_athena_ping is "now" so connect's (>= now-120) check passes
  assert out["last_athena_ping"] == 5000


def test_device_dict_offline_uses_last_seen():
  d = Device(dongle_id="dead0000beef1111", is_online=False, last_seen=_dt(1000))
  out = _device_dict(d, now=5000.0)
  assert out["last_athena_ping"] == 1000


def test_device_dict_defaults_device_type():
  d = Device(dongle_id="dead0000beef1111", is_online=False, last_seen=None)
  out = _device_dict(d, now=5000.0)
  assert out["device_type"] == "threex"
  assert out["last_athena_ping"] == 0


def test_segment_times_from_gps():
  route = Route(id="d|l", dongle_id="d", log_id="l", start_time_utc=_dt(1000))
  segs = [Segment(route_id="d|l", segment_num=0), Segment(route_id="d|l", segment_num=1)]
  pts = [
    GpsPoint(route_id="d|l", segment_num=0, t_utc=_dt(1000), lat=1.0, lon=2.0),
    GpsPoint(route_id="d|l", segment_num=0, t_utc=_dt(1030), lat=1.0, lon=2.0),
    GpsPoint(route_id="d|l", segment_num=1, t_utc=_dt(1060), lat=1.0, lon=2.0),
    GpsPoint(route_id="d|l", segment_num=1, t_utc=_dt(1090), lat=1.0, lon=2.0),
  ]
  starts, ends = _segment_times(route, segs, pts)
  assert starts == [1000_000, 1060_000]
  assert ends == [1030_000, 1090_000]


def test_segment_times_fallback_without_gps():
  route = Route(id="d|l", dongle_id="d", log_id="l", start_time_utc=_dt(1000))
  segs = [Segment(route_id="d|l", segment_num=0), Segment(route_id="d|l", segment_num=2)]
  starts, ends = _segment_times(route, segs, pts=[])
  # seg 0 -> base, seg 2 -> base + 120s; each spans ~60s
  assert starts == [1000_000, 1120_000]
  assert ends == [1060_000, 1180_000]


def test_seg_duration_from_mono():
  seg = Segment(route_id="d|l", segment_num=0, start_mono=1_000_000_000, end_mono=61_000_000_000)
  assert _seg_duration_s(seg) == 60.0


def test_seg_duration_default_when_missing():
  assert _seg_duration_s(Segment(route_id="d|l", segment_num=0)) == 60.0
