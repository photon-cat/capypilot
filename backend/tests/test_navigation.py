"""Navigation serialization tests (pure, no DB)."""
from datetime import datetime, timezone

from app.models import NavDestination, NavLocation
from app.routers.navigation import _destination_dict, _location_dict


def test_destination_dict_shape():
  d = NavDestination(
    dongle_id="0000007b0000007b", place_name="1441 State St",
    place_details="San Diego, CA", latitude=32.71, longitude=-117.16,
  )
  out = _destination_dict(d)
  assert out == {
    "place_name": "1441 State St",
    "place_details": "San Diego, CA",
    "latitude": 32.71,
    "longitude": -117.16,
  }


def test_location_dict_shape():
  loc = NavLocation(
    id=5, dongle_id="0000007b0000007b", save_type="favorite", label="Home",
    place_name="Home", place_details="123 Main", latitude=37.0, longitude=-122.0,
    modified=datetime(2026, 6, 5, tzinfo=timezone.utc),
  )
  out = _location_dict(loc)
  assert out["id"] == 5
  assert out["save_type"] == "favorite"
  assert out["label"] == "Home"
  assert out["latitude"] == 37.0 and out["longitude"] == -122.0
  assert out["modified"].startswith("2026-06-05")
