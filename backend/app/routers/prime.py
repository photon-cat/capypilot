"""comma prime / billing endpoints (connect's BILLING_URL surface).

This backend does not gate anything behind billing: every device is prime, so
`GET /v1/prime/subscription` always returns an active, free ($0) subscription
with a truthy `user_id` (the field connect uses to decide the sub is active).
The remaining stripe/cancel/subscribe_info endpoints are implemented as benign
no-ops for completeness (connect never calls them while `device.prime` is true).

In a self-hosted connect, point `window.BILLING_URL_ROOT` at this backend.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query

from ..config import get_settings
from ..deps import current_user
from ..models import User

router = APIRouter(tags=["prime"])

_YEAR = 365 * 24 * 3600


@router.get("/v1/prime/subscription")
async def prime_subscription(
  dongle_id: str = Query(...), user: User = Depends(current_user)
) -> dict:
  """Active, free subscription. `user_id` truthy => connect shows prime active."""
  now = int(time.time())
  return {
    "user_id": str(user.id),
    "dongle_id": dongle_id,
    "plan": "data",                  # -> connect labels it "Standard"
    "subscribed_at": now - _YEAR,
    "next_charge_at": now + _YEAR,
    "cancel_at": None,
    "amount": 0,                     # cents; ungated => $0.00
    "is_prime_sim": False,
    "requires_migration": False,
    "trial_claimed": True,
  }


@router.get("/v1/prime/subscribe_info")
async def prime_subscribe_info(
  dongle_id: str = Query(...), _: User = Depends(current_user)
) -> dict:
  """Never hit by connect while prime is true; returned for completeness."""
  return {
    "sim_id": f"capy-{dongle_id}",
    "sim_type": "magenta_new",
    "is_prime_sim": False,
    "sim_usable": True,
    "device_online": True,
    "trial_end_data": 0,
    "trial_end_nodata": 0,
  }


@router.post("/v1/prime/cancel")
async def prime_cancel(body: dict, _: User = Depends(current_user)) -> dict:
  # Nothing to cancel (no billing); report success so the UI doesn't error.
  return {"success": True}


@router.post("/v1/prime/stripe_checkout")
async def prime_stripe_checkout(body: dict, _: User = Depends(current_user)) -> dict:
  # No paywall: there is nothing to check out. Send the user back to the app.
  return {"url": get_settings().api_host.rstrip("/")}


@router.get("/v1/prime/stripe_portal")
async def prime_stripe_portal(
  dongle_id: str = Query(...), _: User = Depends(current_user)
) -> dict:
  return {"url": get_settings().api_host.rstrip("/")}


@router.get("/v1/prime/stripe_session")
async def prime_stripe_session(
  dongle_id: str = Query(...), session_id: str = Query(default=""), _: User = Depends(current_user)
) -> dict:
  return {"payment_status": "paid"}
