"""Optional Redis cache helpers with graceful Neon/database fallback.

The web app treats Redis as disposable acceleration: a miss or outage must never
prevent a request from continuing through its normal data-source path.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any

try:
    import redis
    from redis.exceptions import RedisError
except ImportError:  # Keep the app available if an optional cache dependency is absent.
    redis = None

    class RedisError(Exception):
        pass


logger = logging.getLogger(__name__)
CACHE_PREFIX = "xxxworld:v1"

_client = None
_client_url = ""


def cache_key(suffix: str) -> str:
    """Build a versioned key so cache formats can be changed safely."""
    return f"{CACHE_PREFIX}:{suffix}"


def get_redis_client():
    """Return a lazily-created Redis client, or None when caching is disabled."""
    global _client, _client_url

    url = os.getenv("REDIS_URL", "").strip()
    if not url or redis is None:
        return None

    if _client is not None and _client_url == url:
        return _client

    try:
        _client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
            retry_on_timeout=False,
            health_check_interval=30,
            max_connections=16,
        )
        _client_url = url
        return _client
    except (RedisError, TypeError, ValueError):
        _client = None
        _client_url = ""
        logger.debug("Redis client initialization failed; continuing without cache.")
        return None


def _json_default(value: Any):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(value)
    return str(value)


def cache_get_json(key: str) -> Any | None:
    """Read and decode one JSON value. Cache misses and outages return None."""
    client = get_redis_client()
    if client is None:
        return None
    try:
        raw = client.get(key)
        if not raw:
            return None
        return json.loads(raw)
    except (RedisError, TypeError, ValueError):
        logger.debug("Redis cache read failed for key %s.", key)
        return None


def cache_set_json(key: str, value: Any, ttl_seconds: int) -> bool:
    """Write a JSON value with expiry; cache failure is non-fatal."""
    if ttl_seconds <= 0:
        return False
    client = get_redis_client()
    if client is None:
        return False
    try:
        raw = json.dumps(
            value,
            separators=(",", ":"),
            ensure_ascii=False,
            default=_json_default,
        )
        return bool(client.set(key, raw, ex=int(ttl_seconds)))
    except (RedisError, TypeError, ValueError):
        logger.debug("Redis cache write failed for key %s.", key)
        return False
