"""capypilot self-hosted backend.

A drop-in replacement for the comma.ai data backend: device registration,
upload-URL issuance, log indexing, an Athena WebSocket control channel, and a
connect-style web UI. Point a device at it by setting `API_HOST` and
`ATHENA_HOST`.
"""

__version__ = "0.1.0"
