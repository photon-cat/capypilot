"""SQLAlchemy ORM models — the backend's persistent state.

Schema mirrors what comma's backend tracks: devices, routes, segments (with
per-file presence), a decimated GPS trace for the map, an upload audit log, and
device-pushed stats/logs.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
  BigInteger,
  Boolean,
  DateTime,
  Float,
  ForeignKey,
  Integer,
  String,
  UniqueConstraint,
  func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# Per-segment file slots we track presence/size/etag for.
SEGMENT_FILE_KINDS = ("qlog", "rlog", "qcamera", "fcamera", "ecamera", "dcamera")


class Device(Base):
  __tablename__ = "device"

  dongle_id: Mapped[str] = mapped_column(String(16), primary_key=True)
  public_key: Mapped[str] = mapped_column(String, nullable=False)
  jwt_algorithm: Mapped[str] = mapped_column(String(8), nullable=False, default="RS256")
  serial: Mapped[str | None] = mapped_column(String, nullable=True)
  imei: Mapped[str | None] = mapped_column(String, nullable=True)
  imei2: Mapped[str | None] = mapped_column(String, nullable=True)
  alias: Mapped[str | None] = mapped_column(String, nullable=True)
  # connect maps device_type -> a display name ("threex" -> "comma 3X").
  device_type: Mapped[str] = mapped_column(String(16), default="threex", nullable=False)
  # Last openpilot version seen for this device (from a parsed qlog's InitData).
  openpilot_version: Mapped[str | None] = mapped_column(String, nullable=True)
  is_online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
  last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

  routes: Mapped[list[Route]] = relationship(back_populates="device", cascade="all, delete-orphan")


class Route(Base):
  __tablename__ = "route"

  # Canonical name: "{dongle_id}|{log_id}"
  id: Mapped[str] = mapped_column(String, primary_key=True)
  dongle_id: Mapped[str] = mapped_column(ForeignKey("device.dongle_id", ondelete="CASCADE"), index=True, nullable=False)
  log_id: Mapped[str] = mapped_column(String, nullable=False)

  start_time_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  end_time_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  max_segment: Mapped[int] = mapped_column(Integer, default=-1, nullable=False)
  length_m: Mapped[float | None] = mapped_column(Float, nullable=True)

  git_commit: Mapped[str | None] = mapped_column(String, nullable=True)
  version: Mapped[str | None] = mapped_column(String, nullable=True)
  init_params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

  device: Mapped[Device] = relationship(back_populates="routes")
  segments: Mapped[list[Segment]] = relationship(back_populates="route", cascade="all, delete-orphan")


class Segment(Base):
  __tablename__ = "segment"
  __table_args__ = (UniqueConstraint("route_id", "segment_num", name="uq_segment_route_num"),)

  id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
  route_id: Mapped[str] = mapped_column(ForeignKey("route.id", ondelete="CASCADE"), index=True, nullable=False)
  segment_num: Mapped[int] = mapped_column(Integer, nullable=False)

  proc_state: Mapped[str] = mapped_column(String(16), default="uploaded", nullable=False)  # uploaded|parsed|failed
  start_mono: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
  end_mono: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

  # Per-file presence + metadata. Stored as a single jsonb blob keyed by kind:
  #   {"qlog": {"key": "...", "size": 123, "etag": "..."}, ...}
  files: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

  route: Mapped[Route] = relationship(back_populates="segments")


class GpsPoint(Base):
  __tablename__ = "gps_point"

  id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
  route_id: Mapped[str] = mapped_column(ForeignKey("route.id", ondelete="CASCADE"), index=True, nullable=False)
  segment_num: Mapped[int] = mapped_column(Integer, nullable=False)
  t_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
  lat: Mapped[float] = mapped_column(Float, nullable=False)
  lon: Mapped[float] = mapped_column(Float, nullable=False)
  alt: Mapped[float | None] = mapped_column(Float, nullable=True)
  speed: Mapped[float | None] = mapped_column(Float, nullable=True)
  bearing: Mapped[float | None] = mapped_column(Float, nullable=True)


class Upload(Base):
  __tablename__ = "upload"

  id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
  dongle_id: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
  key: Mapped[str] = mapped_column(String, nullable=False)        # storage key {dongle}/{route}/{seg}/{file}
  device_path: Mapped[str] = mapped_column(String, nullable=False)  # the `path` query param from the device
  size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
  status: Mapped[str] = mapped_column(String(16), default="requested", nullable=False)  # requested|stored|exists
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DeviceStat(Base):
  __tablename__ = "device_stat"

  id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
  dongle_id: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
  kind: Mapped[str] = mapped_column(String(16), nullable=False)  # stats|logs
  payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class User(Base):
  __tablename__ = "app_user"

  id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
  email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
  password_hash: Mapped[str] = mapped_column(String, nullable=False)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class NavDestination(Base):
  """The single pending "next" navigation destination for a device.

  Set via `POST /v1/navigation/{id}/set_destination` when the device is offline
  (when online we push it straight to the car over Athena instead). The device
  pulls and clears it via `GET /v1/navigation/{id}/next`.
  """
  __tablename__ = "nav_destination"

  dongle_id: Mapped[str] = mapped_column(String(16), primary_key=True)
  place_name: Mapped[str] = mapped_column(String, nullable=False)
  place_details: Mapped[str | None] = mapped_column(String, nullable=True)
  latitude: Mapped[float] = mapped_column(Float, nullable=False)
  longitude: Mapped[float] = mapped_column(Float, nullable=False)
  modified: Mapped[datetime] = mapped_column(
    DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
  )


class NavLocation(Base):
  """A saved navigation location (favorites + recent destinations)."""
  __tablename__ = "nav_location"

  id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
  dongle_id: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
  save_type: Mapped[str] = mapped_column(String(16), nullable=False)  # favorite|recent|home|work
  label: Mapped[str | None] = mapped_column(String, nullable=True)
  place_name: Mapped[str] = mapped_column(String, nullable=False)
  place_details: Mapped[str | None] = mapped_column(String, nullable=True)
  latitude: Mapped[float] = mapped_column(Float, nullable=False)
  longitude: Mapped[float] = mapped_column(Float, nullable=False)
  modified: Mapped[datetime] = mapped_column(
    DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
  )
