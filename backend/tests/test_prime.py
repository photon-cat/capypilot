"""Prime subscription tests — connect reads these to show prime as active."""
import time

from app.models import User
from app.routers.prime import prime_subscription


async def test_subscription_active_and_free():
  sub = await prime_subscription("0000007b0000007b", user=User(id=7, email="a@b"))
  # connect gates the "active sub" view on a truthy user_id
  assert sub["user_id"] == "7"
  # ungated => $0.00, never expires soon, not cancelled
  assert sub["amount"] == 0
  assert sub["cancel_at"] is None
  assert sub["plan"] in ("data", "nodata")
  now = int(time.time())
  assert sub["subscribed_at"] <= now < sub["next_charge_at"]
