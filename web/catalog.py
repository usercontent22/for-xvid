"""Indexed, cursor-paginated catalog reads from the DB3/DB4 serving shards.

The site does not copy the 1.3M-row catalog into a second database. Each shard
uses a small set of purpose-built indexes, and the application merges only the
top page from each shard. DB5 is reserved for compact homepage snapshots.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from web.cache import cache_get_json, cache_key, cache_set_json
from web.db import DatabaseUnavailable, catalog_sources, fetch_rows
from web.ids import normalize_channel_key


logger = logging.getLogger(__name__)
CATALOG_CACHE_TTL_SECONDS = 300
SEARCH_CACHE_TTL_SECONDS = 90
CATALOG_COLUMNS = """
url, title, img, rating_percentage, views, duration_seconds,
channel_name, tags
"""

SEARCH_VECTOR_SQL = """
to_tsvector(
    'simple'::regconfig,
    COALESCE(title, '') || ' ' ||
    COALESCE(channel_name, '')
)
"""
CHANNEL_KEY_SQL = "regexp_replace(lower(COALESCE(channel_name, '')), '[^a-z0-9]', '', 'g')"


def catalog_configured() -> bool:
    return all(url for _, url in catalog_sources())


def _encode_cursor(row: dict[str, Any], sort: str) -> str:
    payload = {
        "url": row.get("url"),
        "views": int(row["views"]) if row.get("views") is not None else None,
        "rating": float(row["rating_percentage"]) if row.get("rating_percentage") is not None else None,
        "sort": sort,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None, sort: str) -> dict[str, Any] | None:
    if not cursor or len(cursor) > 4096:
        return None
    try:
        raw = base64.urlsafe_b64decode(
            (cursor + "=" * ((4 - len(cursor) % 4) % 4)).encode("ascii")
        )
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, dict) or decoded.get("sort") != sort:
            return None
        if not isinstance(decoded.get("url"), str) or not decoded["url"]:
            return None
        if not isinstance(decoded.get("views"), (int, float)):
            return None
        if sort == "rating" and not isinstance(decoded.get("rating"), (int, float)):
            return None
        return decoded
    except (ValueError, TypeError, UnicodeError):
        return None


def _fetch_shard(
    database_url: str,
    *,
    sort: str,
    cursor: dict[str, Any] | None,
    limit: int,
    channel: str | None,
    tag: str | None,
    search: str | None,
) -> list[dict[str, Any]]:
    filters = ["views IS NOT NULL"]
    params: list[Any] = []

    if channel:
        filters.append(f"{CHANNEL_KEY_SQL} = %s")
        params.append(normalize_channel_key(channel))

    if tag:
        # Exact source-tag match backed by the compact GIN array index.
        filters.append("tags @> ARRAY[%s]::text[]")
        params.append(tag)

    if search:
        # Search title/channel through FTS and allow an exact full-tag match through the
        # small array GIN index. PostgreSQL can combine both indexes using BitmapOr.
        filters.append(
            f"({SEARCH_VECTOR_SQL} @@ websearch_to_tsquery('simple'::regconfig, %s) "
            "OR tags @> ARRAY[%s]::text[])"
        )
        params.extend((search, search))

    if sort == "rating":
        filters.append("rating_percentage IS NOT NULL")

    if cursor:
        url = cursor["url"]
        views = int(cursor["views"])
        if sort == "rating":
            rating = float(cursor["rating"])
            filters.append(
                "("
                "rating_percentage < %s "
                "OR (rating_percentage = %s AND views < %s) "
                "OR (rating_percentage = %s AND views = %s AND url < %s)"
                ")"
            )
            params.extend((rating, rating, views, rating, views, url))
        else:
            filters.append("(views < %s OR (views = %s AND url < %s))")
            params.extend((views, views, url))

    order_by = (
        "rating_percentage DESC, views DESC, url DESC"
        if sort == "rating"
        else "views DESC, url DESC"
    )
    sql = f"""
        SELECT {CATALOG_COLUMNS}
        FROM public.videos
        WHERE {" AND ".join(filters)}
        ORDER BY {order_by}
        LIMIT %s
    """
    params.append(min(max(int(limit), 1), 48) + 1)
    return fetch_rows(database_url, sql, tuple(params), timeout_ms=6500)


def _sort_rows(rows: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    # Stable sorts implement the same multi-column DESC order across shards.
    rows.sort(key=lambda row: str(row.get("url") or ""), reverse=True)
    rows.sort(key=lambda row: int(row.get("views") or 0), reverse=True)
    if sort == "rating":
        rows.sort(
            key=lambda row: float(row.get("rating_percentage") or 0),
            reverse=True,
        )
    return rows


def _catalog_cache_key(
    *,
    sort: str,
    cursor: str | None,
    limit: int,
    channel: str | None,
    tag: str | None,
    search: str | None,
) -> str:
    # Hash parameters so arbitrary search text never becomes a Redis key.
    parameters = {
        "sort": sort,
        "cursor": cursor or "",
        "limit": int(limit),
        "channel": channel or "",
        "tag": tag or "",
        "search": search or "",
    }
    signature = json.dumps(parameters, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()
    return cache_key(f"catalog:{digest}")


def list_catalog(
    *,
    sort: str = "views",
    cursor: str | None = None,
    limit: int = 24,
    channel: str | None = None,
    tag: str | None = None,
    search: str | None = None,
) -> dict[str, Any] | None:
    """Merge bounded top pages from each indexed shard, with a short Redis cache."""
    if sort not in {"views", "rating"}:
        sort = "views"
    page_size = min(max(int(limit), 1), 48)
    sources = [(name, url) for name, url in catalog_sources() if url]
    if not sources:
        return None

    cache_key_value = _catalog_cache_key(
        sort=sort,
        cursor=cursor,
        limit=page_size,
        channel=channel,
        tag=tag,
        search=search,
    )
    cached = cache_get_json(cache_key_value)
    if (
        isinstance(cached, dict)
        and isinstance(cached.get("items"), list)
        and all(isinstance(item, dict) for item in cached["items"])
        and (cached.get("next_cursor") is None or isinstance(cached.get("next_cursor"), str))
    ):
        return cached

    current = _decode_cursor(cursor, sort)
    rows_by_shard: list[list[dict[str, Any]]] = []
    failures = 0
    with ThreadPoolExecutor(max_workers=len(sources)) as executor:
        futures = {
            executor.submit(
                _fetch_shard,
                url,
                sort=sort,
                cursor=current,
                limit=page_size,
                channel=channel,
                tag=tag,
                search=search,
            ): name
            for name, url in sources
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                rows_by_shard.append(future.result())
            except DatabaseUnavailable:
                failures += 1
                logger.warning("Video catalog shard %s is temporarily unavailable.", name)

    if not rows_by_shard or failures == len(sources):
        return None

    # Migration is partitioned by URL; still dedupe defensively in case the
    # destination sets contain a historical overlap.
    unique: dict[str, dict[str, Any]] = {}
    for rows in rows_by_shard:
        for row in rows:
            source_url = row.get("url")
            if source_url:
                unique.setdefault(str(source_url), row)

    merged = _sort_rows(list(unique.values()), sort)
    has_more = len(merged) > page_size
    items = merged[:page_size]
    next_cursor = _encode_cursor(items[-1], sort) if has_more and items else None
    result = {"items": items, "has_more": has_more, "next_cursor": next_cursor}

    # Do not cache partial results when one database shard failed; a transient
    # outage must not turn a partial page into a normal cached response.
    if failures == 0:
        ttl = SEARCH_CACHE_TTL_SECONDS if search else CATALOG_CACHE_TTL_SECONDS
        cache_set_json(cache_key_value, result, ttl)

    return result
