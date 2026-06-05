"""Application configuration, loaded from environment / `.env`."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Default cereal schema location: the vendored backend/schema dir (self-contained,
# no opendbc submodule needed). In Docker this is overridden by CEREAL_PATH=/app/cereal.
_REPO_CEREAL = Path(__file__).resolve().parents[1] / "schema"


class Settings(BaseSettings):
  model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

  # Database
  database_url: str = "postgresql+asyncpg://capy:capy_dev_password@localhost:5432/capypilot"

  # Object storage (S3 / MinIO)
  s3_endpoint_url: str = "http://localhost:9000"          # used by the backend (internal network)
  s3_public_endpoint_url: str = "http://localhost:9000"   # used in presigned URLs handed to device/browser
  s3_access_key: str = "capyminio"
  s3_secret_key: str = "capyminio_dev_password"
  s3_bucket: str = "capypilot-data"
  s3_region: str = "us-east-1"

  upload_url_ttl: int = 3600       # presigned PUT lifetime (seconds)
  download_url_ttl: int = 86400    # presigned GET lifetime (seconds)

  # Auth
  user_jwt_secret: str = "change_me_to_a_long_random_string"
  user_jwt_ttl: int = 7 * 24 * 3600
  admin_email: str = "admin@local"
  admin_password: str = "admin"

  # Cap'n Proto schema directory (contains log.capnp + imports)
  cereal_path: str = str(_REPO_CEREAL)

  # How many GPS points to keep per segment for the map trace (decimation).
  gps_points_per_segment: int = 60

  # Internal URL the API uses to reach the athena service's HTTP control surface.
  athena_internal_url: str = "http://athena:8001"

  # Informational / browser-facing.
  # `api_host` doubles as the public base URL embedded in connect's
  # `routes_segments` responses (the per-route `url` the browser fetches
  # coords.json/events.json from), so it must be reachable from the browser.
  api_host: str = "http://localhost:8000"
  athena_host: str = "ws://localhost:8001"

  # CORS: connect is served from a different origin and calls this API with an
  # `Authorization: JWT` header (no cookies), so a wildcard origin is safe here.
  # Set to a comma-separated allowlist (e.g. "https://connect.example.com") to
  # lock it down. "*" allows any origin.
  cors_origins: str = "*"

  # Uploads we accept indexing for (matches loggerd output)
  known_filenames: tuple[str, ...] = (
    "qlog", "qlog.zst", "qlog.bz2",
    "rlog", "rlog.zst", "rlog.bz2",
    "qcamera.ts",
    "fcamera.hevc", "ecamera.hevc", "dcamera.hevc",
  )


  def cors_origin_list(self) -> list[str]:
    return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
  return Settings()
