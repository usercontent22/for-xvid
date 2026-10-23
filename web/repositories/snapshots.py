"""Small, indexed homepage-snapshot reads from the dedicated DB5 store."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import psycopg


logger = logging.getLogger(__name__)
HOME_SECTIONS = ("featured", "popular", "discovery", "tags")


def _payload(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            result = json.loads(value)
            return result if isinstance(result, dict) else {}
        except ValueError:
            return {}
    return {}


def load_homepage_data() -> dict[str, dict]:
    """Read compact prepared feeds from DB5; never reach back to DB1 or DB2."""
    url = os.getenv("DATABASE_URL_5", "").strip()
    if not url:
        logger.warning("Homepage snapshot store DATABASE_URL_5 is not configured.")
        return {}

    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date()
    sections = list(HOME_SECTIONS)
    sql = """
        WITH chosen_day AS (
            SELECT feed_date
            FROM public.homepage_snapshots
            WHERE section = 'featured'
            ORDER BY ABS(feed_date - %s::date),
                     CASE WHEN feed_date <= %s::date THEN 0 ELSE 1 END
            LIMIT 1
        )
        SELECT section, payload, feed_date, generated_at
        FROM public.homepage_snapshots
        WHERE feed_date = (SELECT feed_date FROM chosen_day)
          AND section = ANY(%s::text[])
        UNION ALL
        SELECT section, payload, NULL::date AS feed_date, generated_at
        FROM (
            SELECT 'tag_catalog'::text AS section, payload, generated_at
            FROM public.homepage_snapshots
            WHERE section = 'tag_catalog'
            ORDER BY generated_at DESC
            LIMIT 1
        ) AS last_tag_catalog
    """
    try:
        with psycopg.connect(
            url,
            connect_timeout=4,
            application_name="xxxworld-homepage-db5",
            prepare_threshold=None,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('statement_timeout', %s, true)", ("3500ms",))
                cur.execute(sql, (today, today, sections))
                rows = cur.fetchall()
    except psycopg.Error:
        logger.warning("DB5 homepage snapshot read failed; serving an empty snapshot state.")
        return {}

    output: dict[str, dict] = {}
    for section, payload, _feed_date, _generated_at in rows:
        output[str(section)] = _payload(payload)
    if "tag_catalog" not in output and "tags" in output:
        output["tag_catalog"] = output["tags"]
    return output
