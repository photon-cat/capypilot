"""S3 / MinIO object storage helpers.

Large artifacts never flow through the API process: the device PUTs directly to
a presigned URL, and the browser GETs directly from one. The API only signs
URLs (a local operation) and does lightweight metadata calls (head/list).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from .config import get_settings


@dataclass(frozen=True)
class ObjectInfo:
  key: str
  size: int
  etag: str


def _make_client(endpoint_url: str):
  s = get_settings()
  return boto3.client(
    "s3",
    endpoint_url=endpoint_url,
    aws_access_key_id=s.s3_access_key,
    aws_secret_access_key=s.s3_secret_key,
    region_name=s.s3_region,
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
  )


@lru_cache
def _internal_client():
  """Client used for server-side calls (head/list/get) over the internal network."""
  return _make_client(get_settings().s3_endpoint_url)


@lru_cache
def _public_client():
  """Client used to sign URLs handed to the device/browser (public endpoint)."""
  return _make_client(get_settings().s3_public_endpoint_url)


def build_key(dongle_id: str, route: str, segment: str, filename: str) -> str:
  """Storage key layout REQUIRED by openpilot tooling.

  `tools/lib/route.py` parses file URLs with `path.rsplit('/', 4)` expecting
  `.../{dongle_id}/{time_str}/{segment_num}/{filename}`, so we must store at
  exactly this shape.
  """
  return f"{dongle_id}/{route}/{segment}/{filename}"


def presign_put(key: str, content_type: str | None = None) -> tuple[str, dict[str, str]]:
  """Return (url, headers) for a direct device->storage upload."""
  s = get_settings()
  params = {"Bucket": s.s3_bucket, "Key": key}
  headers: dict[str, str] = {}
  if content_type:
    params["ContentType"] = content_type
    headers["Content-Type"] = content_type
  url = _public_client().generate_presigned_url("put_object", Params=params, ExpiresIn=s.upload_url_ttl)
  return url, headers


def presign_get(key: str) -> str:
  s = get_settings()
  return _public_client().generate_presigned_url(
    "get_object", Params={"Bucket": s.s3_bucket, "Key": key}, ExpiresIn=s.download_url_ttl
  )


def _head_sync(key: str) -> ObjectInfo | None:
  s = get_settings()
  try:
    resp = _internal_client().head_object(Bucket=s.s3_bucket, Key=key)
  except ClientError as e:
    if e.response.get("ResponseMetadata", {}).get("HTTPStatusCode") in (404, 403):
      return None
    raise
  return ObjectInfo(key=key, size=int(resp.get("ContentLength", 0)), etag=resp.get("ETag", "").strip('"'))


async def head_object(key: str) -> ObjectInfo | None:
  return await asyncio.to_thread(_head_sync, key)


def _get_bytes_sync(key: str) -> bytes:
  s = get_settings()
  resp = _internal_client().get_object(Bucket=s.s3_bucket, Key=key)
  return resp["Body"].read()


async def get_bytes(key: str) -> bytes:
  return await asyncio.to_thread(_get_bytes_sync, key)


def _ensure_bucket_sync() -> None:
  s = get_settings()
  client = _internal_client()
  try:
    client.head_bucket(Bucket=s.s3_bucket)
  except ClientError:
    client.create_bucket(Bucket=s.s3_bucket)


async def ensure_bucket() -> None:
  await asyncio.to_thread(_ensure_bucket_sync)
