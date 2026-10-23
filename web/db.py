"""Short-lived PostgreSQL connections and URL-sharded video lookup."""
from __future__ import annotations

import hashlib
import os
from typing import Any, Sequence

import psycopg
from psycopg.rows import dict_row


VIDEO_COLUMNS = """
url, title, img, rating, rating_percentage, views, duration,
duration_seconds, channel_name, tags
"""


class DatabaseUnavailable(RuntimeError):
    """Raised when the required database sources cannot be reached."""


def connection_url(name: str) -> str:
    return os.getenv(name, "").strip()


def fetch_rows(
    url: str,
    sql: str,
    params: Sequence[Any] = (),
    *,
    timeout_ms: int = 6500,
) -> list[dict[str, Any]]:
    if not url:
        raise DatabaseUnavailable("Database connection is not configured.")
    try:
        with psycopg.connect(
            url,
            connect_timeout=4,
            application_name="xxxworld-web",
            prepare_threshold=None,
            row_factory=dict_row,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (f"{timeout_ms}ms",),
                )
                cur.execute(sql, params)
                return list(cur.fetchall())
    except psycopg.Error as exc:
        # Never expose connection strings or credentials in logs/errors.
        raise DatabaseUnavailable("A database request failed.") from exc


def catalog_sources() -> list[tuple[str, str]]:
    """Return only the active serving shards. DB1/DB2 are intentionally unused."""
    return [
        ("DATABASE_URL_3", connection_url("DATABASE_URL_3")),
        ("DATABASE_URL_4", connection_url("DATABASE_URL_4")),
    ]


def catalog_configured() -> bool:
    return all(url for _, url in catalog_sources())


def preferred_video_shard(source_url: str) -> str:
    """Match the original stable SHA-256 partition: DB3=0, DB4=1."""
    bucket = hashlib.sha256(source_url.encode("utf-8")).digest()[0] % 2
    return "DATABASE_URL_3" if bucket == 0 else "DATABASE_URL_4"


def find_video(source_url: str) -> dict[str, Any] | None:
    """Use the expected shard's URL primary key, with one fallback for resiliency."""
    sql = f"SELECT {VIDEO_COLUMNS} FROM public.videos WHERE url = %s LIMIT 1"
    preferred = preferred_video_shard(source_url)
    fallback = "DATABASE_URL_4" if preferred == "DATABASE_URL_3" else "DATABASE_URL_3"
    configured = 0
    failures = 0

    for env_name in (preferred, fallback):
        url = connection_url(env_name)
        if not url:
            continue
        configured += 1
        try:
            rows = fetch_rows(url, sql, (source_url,), timeout_ms=3000)
        except DatabaseUnavailable:
            failures += 1
            continue
        if rows:
            return rows[0]

    if configured == 0 or failures == configured:
        raise DatabaseUnavailable("Video data is temporarily unavailable.")
    return None
