"""FastAPI application factory for the capypilot backend REST API."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from . import __version__
from .auth import hash_password
from .config import get_settings
from .db import SessionLocal
from .models import User
from .routers import connect, internal, navigation, pilotauth, prime, routes, ui_api, upload
from .storage import ensure_bucket

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("capypilot.api")


async def _seed_admin() -> None:
  s = get_settings()
  async with SessionLocal() as session:
    existing = await session.scalar(select(User).where(User.email == s.admin_email))
    if existing is None:
      session.add(User(email=s.admin_email, password_hash=hash_password(s.admin_password)))
      await session.commit()
      log.info("seeded admin user %s", s.admin_email)


@asynccontextmanager
async def lifespan(app: FastAPI):
  try:
    await ensure_bucket()
  except Exception:  # storage may not be up yet in some dev flows; don't crash boot
    log.exception("ensure_bucket failed (continuing)")
  try:
    await _seed_admin()
  except Exception:
    log.exception("seed_admin failed (continuing)")
  yield


def create_app() -> FastAPI:
  app = FastAPI(title="capypilot backend", version=__version__, lifespan=lifespan)

  # CORS: the connect web app calls this API from a different origin using an
  # `Authorization: JWT` header (no cookies), so a wildcard origin is safe.
  origins = get_settings().cors_origin_list()
  app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=("*" not in origins),
    allow_methods=["*"],
    allow_headers=["*"],
  )

  # Device-facing contracts (must match comma's API exactly)
  app.include_router(pilotauth.router)
  app.include_router(upload.router)
  app.include_router(routes.router)
  # commaai/connect compatibility layer
  app.include_router(connect.router)
  app.include_router(prime.router)
  app.include_router(navigation.router)
  # Web UI JSON API
  app.include_router(ui_api.router)
  # Internal (storage notifications)
  app.include_router(internal.router)

  @app.get("/health")
  async def health() -> dict:
    return {"status": "ok", "version": __version__}

  # Web UI (served by the API): static assets + SPA entrypoint.
  web_dir = Path(__file__).parent / "web"
  app.mount("/static", StaticFiles(directory=web_dir), name="static")

  @app.get("/")
  async def index() -> FileResponse:
    return FileResponse(web_dir / "index.html")

  return app


app = create_app()
