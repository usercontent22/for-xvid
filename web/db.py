"""Small, short-lived PostgreSQL connections suitable for Vercel Functions."""
from __future__ import annotations

import os
from typing import Any, Sequence

import psycopg
from psycopg.rows import dict_row


VIDEO_COLUMNS = """
url, title, img, rating, rating_percentage, views, duration,
duration_seconds, channel_name, tags
"""


class DatabaseUnavailable(RuntimeError):
    """Raised when a configured database cannot be reached."""


def connection_url(name: str) -> str:
    return os.getenv(name, "").strip()


def fetch_rows(
    url: str,
    sql: str,
    params: Sequence[Any] = (),
    *,
    timeout_ms: int = 4000,
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
                cur.execute("SELECT set_config('statement_timeout', %s, true)", (f"{timeout_ms}ms",))
                cur.execute(sql, params)
                return list(cur.fetchall())
    except psycopg.Error as exc:
        # Do not include a connection string or database credentials in logs/errors.
        raise DatabaseUnavailable("A database request failed.") from exc


def find_video(source_url: str) -> dict[str, Any] | None:
    """Use the existing URL primary key; never scan the video tables."""
    sql = f"SELECT {VIDEO_COLUMNS} FROM public.videos WHERE url = %s LIMIT 1"
    failures = 0
    configured = 0

    for env_name in ("DATABASE_URL_1", "DATABASE_URL_2"):
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
