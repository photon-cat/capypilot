"""Internal endpoints — not device- or user-facing.

MinIO is configured to POST a bucket notification here whenever an object is
created, which drives the ingest worker. This endpoint lives on the internal
Docker network; harden with a shared secret before exposing publicly.
"""
from __future__ import annotations

import logging
from urllib.parse import unquote

from fastapi import APIRouter, Request

from ..db import SessionLocal
from ..ingest.worker import process_object

router = APIRouter(prefix="/internal", tags=["internal"])
log = logging.getLogger("capypilot.internal")


@router.post("/s3-event")
async def s3_event(request: Request) -> dict:
  body = await request.json()
  records = body.get("Records", []) if isinstance(body, dict) else []
  processed = 0
  for rec in records:
    event = rec.get("eventName", "")
    if not event.startswith("s3:ObjectCreated"):
      continue
    s3 = rec.get("s3", {})
    key = unquote(s3.get("object", {}).get("key", ""))
    if not key:
      continue
    try:
      async with SessionLocal() as session:
        await process_object(session, key)
      processed += 1
    except Exception:
      log.exception("ingest failed for key %s", key)
  return {"processed": processed}


@router.post("/reindex")
async def reindex(key: str) -> dict:
  """Manually (re)index a single object key. Useful when notifications are off."""
  async with SessionLocal() as session:
    await process_object(session, key)
  return {"reindexed": key}
