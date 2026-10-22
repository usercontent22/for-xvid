"""Stable, reversible public route keys (no schema migration required)."""
from __future__ import annotations

import base64
import json
import re
import unicodedata
from typing import Any


def encode_token(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def decode_token(token: str | None) -> str | None:
    if not token or len(token) > 4096:
        return None
    try:
        padded = token + ("=" * ((4 - len(token) % 4) % 4))
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeError):
        return None
    if not decoded or "\x00" in decoded or len(decoded) > 2048:
        return None
    return decoded


def slugify(value: str | None) -> str:
    raw = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", raw.casefold()).strip("-") or "item"


def normalize_channel_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def decode_history(token: str | None) -> list[str]:
    decoded = decode_token(token)
    if decoded is None:
        return []
    try:
        values: Any = json.loads(decoded)
    except (TypeError, ValueError):
        return []
    if not isinstance(values, list) or len(values) > 30:
        return []
    return [value if isinstance(value, str) else "" for value in values]


def encode_history(values: list[str]) -> str | None:
    if not values:
        return None
    return encode_token(json.dumps(values[-30:], separators=(",", ":")))
