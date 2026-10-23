"""Redis-first homepage feed reads with DB5 as a short-term safety fallback."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg

from web.cache import RedisError, cache_key, cache_set_json, get_redis_client


logger = logging.getLogger(__name__)
HOME_SECTIONS = ("featured", "popular", "discovery", "tags")
FEED_TTL_SECONDS = 9 * 24 * 60 * 60
FALLBACK_CACHE_TTL_SECONDS = 60 * 60
FEED_LOOKBACK_DAYS = 9
FEED_LOOKAHEAD_DAYS = 2


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


def _valid_feed(value: Any) -> bool:
    payload = _payload(value)
    return isinstance(payload.get("items"), list)


def _read_redis_homepage_data() -> dict[str, dict] | None:
    """Read one nearest prepared feed day using a small number of Redis commands."""
    client = get_redis_client()
    if client is None:
        return None

    today = datetime.now(timezone.utc).date()
    offsets = sorted(
        range(-FEED_LOOKBACK_DAYS, FEED_LOOKAHEAD_DAYS + 1),
        key=lambda offset: (abs(offset), 0 if offset <= 0 else 1),
    )
    chosen_day = None
    featured = None

    try:
        for offset in offsets:
            candidate = today + timedelta(days=offset)
            raw = client.get(cache_key(f"homepage:feed:{candidate.isoformat()}:featured"))
            payload = _payload(raw)
            if _valid_feed(payload):
                chosen_day = candidate
                featured = payload
                break

        if chosen_day is None or featured is None:
            return None

        sections = ("popular", "discovery", "tags")
        keys = [
            cache_key(f"homepage:feed:{chosen_day.isoformat()}:{section}")
            for section in sections
        ]
        keys.append(cache_key("homepage:tag_catalog"))
        values = client.mget(keys)

        output: dict[str, dict] = {"featured": featured}
        for section, raw in zip((*sections, "tag_catalog"), values):
            payload = _payload(raw)
            if payload:
                output[section] = payload

        # A canonical weekly tag catalog is preferred; the date-specific tags
        # payload is a valid fallback if that key has expired.
        if "tag_catalog" not in output and "tags" in output:
            output["tag_catalog"] = output["tags"]

        if any(section not in output for section in ("popular", "discovery")):
            return None
        return output
    except RedisError:
        logger.debug("Redis homepage read failed; falling back to DB5.")
        return None


def _load_db5_homepage_data() -> dict[str, dict]:
    """Read prepared feeds from DB5 only when Redis is empty or unavailable."""
    url = os.getenv("DATABASE_URL_5", "").strip()
    if not url:
        logger.warning("Neither Redis homepage feeds nor DATABASE_URL_5 fallback is available.")
        return {}

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
            application_name="xxxworld-homepage-db5-fallback",
            prepare_threshold=None,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('statement_timeout', %s, true)", ("3500ms",))
                cur.execute(sql, (today, today, sections))
                rows = cur.fetchall()
    except psycopg.Error:
        logger.warning("DB5 homepage fallback read failed.")
        return {}

    output: dict[str, dict] = {}
    for section, payload, _feed_date, _generated_at in rows:
        output[str(section)] = _payload(payload)
    if "tag_catalog" not in output and "tags" in output:
        output["tag_catalog"] = output["tags"]
    return output


def _warm_redis_from_fallback(data: dict[str, dict]) -> None:
    """Briefly cache DB5 results to avoid repeated fallback reads during an outage."""
    featured = data.get("featured", {})
    feed_date = featured.get("date")
    if not isinstance(feed_date, str) or len(feed_date) != 10:
        feed_date = datetime.now(timezone.utc).date().isoformat()

    for section in HOME_SECTIONS:
        payload = data.get(section)
        if payload:
            cache_set_json(
                cache_key(f"homepage:feed:{feed_date}:{section}"),
                payload,
                FALLBACK_CACHE_TTL_SECONDS,
            )

    tag_catalog = data.get("tag_catalog")
    if tag_catalog:
        cache_set_json(
            cache_key("homepage:tag_catalog"),
            tag_catalog,
            FALLBACK_CACHE_TTL_SECONDS,
        )


def load_homepage_data() -> dict[str, dict]:
    """Serve Redis-cached feeds first, falling back to compact DB5 snapshots."""
    cached = _read_redis_homepage_data()
    if cached is not None:
        return cached

    fallback = _load_db5_homepage_data()
    if fallback:
        _warm_redis_from_fallback(fallback)
    return fallback
