"""Pydantic request/response models."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class PilotAuthResponse(BaseModel):
  dongle_id: str


class UploadUrlResponse(BaseModel):
  url: str
  headers: dict[str, str] = {}


class DeviceOut(BaseModel):
  dongle_id: str
  alias: str | None = None
  serial: str | None = None
  is_online: bool = False
  last_seen: datetime | None = None
  created_at: datetime


class SegmentOut(BaseModel):
  segment_num: int
  proc_state: str
  files: dict


class RouteOut(BaseModel):
  fullname: str
  dongle_id: str
  start_time: datetime | None = None
  end_time: datetime | None = None
  segment_count: int
  maxqlog: int
  length_m: float | None = None
  version: str | None = None
  git_commit: str | None = None


class LoginRequest(BaseModel):
  email: str
  password: str


class TokenResponse(BaseModel):
  access_token: str
  token_type: str = "bearer"
