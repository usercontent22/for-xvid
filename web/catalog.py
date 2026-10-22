"""Optional dedicated read-catalog queries with cursor pagination.

The two source databases are deliberately not searched with table scans. Until
CATALOG_DATABASE_URL points to the separately indexed read catalog, browse and
search pages use only precomputed homepage candidates.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

from web.db import DatabaseUnavailable, fetch_rows
from web.ids import normalize_channel_key


logger = logging.getLogger(__name__)
CATALOG_COLUMNS = """
url, title, img, rating_percentage, views, duration_seconds,
channel_name, tags
"""


def catalog_configured() -> bool:
    return bool(os.getenv("CATALOG_DATABASE_URL", "").strip())


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
        raw = base64.urlsafe_b64decode((cursor + "=" * ((4 - len(cursor) % 4) % 4)).encode("ascii"))
        decoded = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        return None
    if not isinstance(decoded, dict) or decoded.get("sort") != sort or not decoded.get("url"):
        return None
    return decoded


def list_catalog(
    *,
    sort: str = "views",
    cursor: str | None = None,
    limit: int = 24,
    channel: str | None = None,
    tag: str | None = None,
    search: str | None = None,
) -> dict[str, Any] | None:
    """Return a keyset-paginated page, without OFFSET or COUNT(*)."""
    url = os.getenv("CATALOG_DATABASE_URL", "").strip()
    if not url:
        return None

    order_by = (
        "rating_percentage DESC NULLS LAST, views DESC NULLS LAST, url ASC"
        if sort == "rating"
        else "views DESC NULLS LAST, url ASC"
    )
    filters: list[str] = []
    params: list[Any] = []
    if channel:
        filters.append("channel_key = %s")
        params.append(normalize_channel_key(channel))
    if tag:
        filters.append("tag_keys @> ARRAY[lower(%s)]::text[]")
        params.append(tag)
    if search:
        filters.append("search_vector @@ websearch_to_tsquery('simple', %s)")
        params.append(search)

    current = _decode_cursor(cursor, sort)
    cursor_sql = ""
    if current:
        source_url = current["url"]
        if sort == "rating":
            rating = current.get("rating")
            views = current.get("views")
            if rating is None:
                if views is None:
                    cursor_sql = "(rating_percentage IS NULL AND views IS NULL AND url > %s)"
                    params.append(source_url)
                else:
                    cursor_sql = "(rating_percentage IS NULL AND (views < %s OR views IS NULL OR (views = %s AND url > %s)))"
                    params.extend((views, views, source_url))
            else:
                parts = ["rating_percentage < %s", "rating_percentage IS NULL"]
                params.append(rating)
                if views is None:
                    parts.append("(rating_percentage = %s AND views IS NULL AND url > %s)")
                    params.extend((rating, source_url))
                else:
                    parts.append("(rating_percentage = %s AND (views < %s OR views IS NULL OR (views = %s AND url > %s)))")
                    params.extend((rating, views, views, source_url))
                cursor_sql = "(" + " OR ".join(parts) + ")"
        else:
            views = current.get("views")
            if views is None:
                cursor_sql = "(views IS NULL AND url > %s)"
                params.append(source_url)
            else:
                cursor_sql = "(views < %s OR (views = %s AND url > %s) OR views IS NULL)"
                params.extend((views, views, source_url))
    if cursor_sql:
        filters.append(cursor_sql)

    where_clause = " AND ".join(filters) if filters else "TRUE"
    sql = f"""
        SELECT {CATALOG_COLUMNS}
        FROM public.catalog_videos
        WHERE {where_clause}
        ORDER BY {order_by}
        LIMIT %s
    """
    params.append(min(max(int(limit), 1), 48) + 1)
    try:
        rows = fetch_rows(url, sql, tuple(params), timeout_ms=6500)
    except DatabaseUnavailable:
        logger.warning("Read-catalog query failed; using the bounded snapshot fallback.")
        return None

    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = _encode_cursor(items[-1], sort) if has_more and items else None
    return {"items": items, "has_more": has_more, "next_cursor": next_cursor}
