#!/usr/bin/env python3
"""Build precomputed, diverse homepage feeds from both source databases."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

import psycopg
from psycopg.types.json import Jsonb

try:
    from scripts.catalog_utils import filter_generic_tags, normalized_tag_key
except ModuleNotFoundError:  # support direct execution: python scripts/build_homepage_snapshots.py
    from catalog_utils import filter_generic_tags, normalized_tag_key

CANDIDATES_PER_QUERY = 3000
FEED_LIMIT = 48
TAG_REFRESH_DAYS = 7
TAG_CATALOG_LIMIT = 60
BASE_SECTIONS = ("featured", "popular", "discovery", "tags")

CREATE_SNAPSHOT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.homepage_snapshots (
    feed_date DATE NOT NULL,
    section TEXT NOT NULL,
    payload JSONB NOT NULL,
    source_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (feed_date, section)
)
"""

VIEWS_CANDIDATES_SQL = """
WITH candidates AS (
    SELECT url
    FROM public.videos
    WHERE views IS NOT NULL
    ORDER BY views DESC NULLS LAST, url
    LIMIT %s
)
SELECT v.url, v.title, v.img, v.rating_percentage, v.views,
       v.duration_seconds, v.channel_name, v.tags
FROM candidates c
JOIN public.videos v USING (url)
"""

RATING_CANDIDATES_SQL = """
WITH candidates AS (
    SELECT url
    FROM public.videos
    WHERE rating_percentage IS NOT NULL
    ORDER BY rating_percentage DESC NULLS LAST, views DESC NULLS LAST, url
    LIMIT %s
)
SELECT v.url, v.title, v.img, v.rating_percentage, v.views,
       v.duration_seconds, v.channel_name, v.tags
FROM candidates c
JOIN public.videos v USING (url)
"""

TAG_CATALOG_SQL = """
WITH expanded AS (
    SELECT
        tag,
        regexp_replace(lower(btrim(tag)), '[^[:alnum:]]', '', 'g') AS norm
    FROM public.videos
    CROSS JOIN LATERAL unnest(tags) AS tag
),
grouped AS (
    SELECT norm, MIN(BTRIM(tag)) AS display_tag, COUNT(*) AS uses
    FROM expanded
    WHERE norm <> ''
      AND norm <> ALL(%s)
    GROUP BY norm
)
SELECT norm, display_tag, uses
FROM grouped
ORDER BY uses DESC, norm
LIMIT %s
"""

UPSERT_SNAPSHOT_SQL = """
INSERT INTO public.homepage_snapshots
    (feed_date, section, payload, source_counts, generated_at)
VALUES (%s, %s, %s, %s, now())
ON CONFLICT (feed_date, section) DO UPDATE SET
    payload = EXCLUDED.payload,
    source_counts = EXCLUDED.source_counts,
    generated_at = EXCLUDED.generated_at
"""


def connect(url: str, label: str):
    return psycopg.connect(
        url,
        connect_timeout=25,
        application_name=f"homepage-snapshot-{label}",
        prepare_threshold=None,
    )


def fingerprint(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    host = re.sub(r"-pooler(?=\.|$)", "", parsed.hostname or "").lower()
    return host, parsed.path


def json_video(row) -> dict | None:
    url, title, img, rating, views, duration_seconds, channel, raw_tags = row
    if not url or not title or not img:
        return None
    tags = filter_generic_tags(raw_tags or [])
    try:
        rating_value = float(rating) if rating is not None else 0.0
    except (TypeError, ValueError):
        rating_value = 0.0
    rating_value = min(100.0, max(0.0, rating_value))
    try:
        view_value = max(0, int(views or 0))
    except (TypeError, ValueError):
        view_value = 0

    view_score = min(1.0, math.log10(view_value + 1) / 7.0)
    rating_score = rating_value / 100.0
    completeness = 1.0  # required title + thumbnail are present at this point
    score = 0.55 * rating_score + 0.35 * view_score + 0.10 * completeness
    channel_label = (channel or "").strip()
    return {
        "url": url,
        "title": title.strip(),
        "img": img,
        "rating_percentage": round(rating_value, 2) if rating is not None else None,
        "views": view_value,
        "duration_seconds": int(duration_seconds) if duration_seconds is not None else None,
        "channel_name": channel_label,
        "tags": tags,
        "primary_tag": tags[0] if tags else "",
        "quality_score": round(score, 6),
        "_channel_key": normalized_tag_key(channel_label) or "unknown-channel",
        "_tag_key": normalized_tag_key(tags[0]) if tags else "untagged",
    }


def get_candidates(connection_url: str, label: str, limit: int) -> list[dict]:
    videos_by_url: dict[str, dict] = {}
    with connect(connection_url, label) as conn:
        with conn.cursor() as cur:
            for query in (VIEWS_CANDIDATES_SQL, RATING_CANDIDATES_SQL):
                cur.execute(query, (limit,))
                for row in cur.fetchall():
                    video = json_video(row)
                    if video is None:
                        continue
                    previous = videos_by_url.get(video["url"])
                    if previous is None or video["quality_score"] > previous["quality_score"]:
                        videos_by_url[video["url"]] = video
    return list(videos_by_url.values())


def get_tag_catalog(connection_urls: tuple[str, str]) -> list[dict]:
    totals: dict[str, dict] = {}
    generic_keys = ["video", "videos", "xvideo", "xvideos", "xvideoscom"]
    for index, url in enumerate(connection_urls, start=1):
        with connect(url, f"db{index}-tags") as conn:
            with conn.cursor() as cur:
                cur.execute(TAG_CATALOG_SQL, (generic_keys, TAG_CATALOG_LIMIT * 2))
                rows = cur.fetchall()
        for norm, display_tag, uses in rows:
            if norm in generic_keys:
                continue
            current = totals.get(norm)
            if current is None:
                totals[norm] = {"key": norm, "tag": display_tag, "uses": int(uses)}
            else:
                current["uses"] += int(uses)
                if len(display_tag or "") < len(current["tag"] or ""):
                    current["tag"] = display_tag
    return sorted(totals.values(), key=lambda item: (-item["uses"], item["key"]))[:TAG_CATALOG_LIMIT]


def load_recent_tag_catalog(catalog_url: str, today: date):
    try:
        with connect(catalog_url, "tag-cache") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT payload, generated_at
                    FROM public.homepage_snapshots
                    WHERE section = 'tag_catalog'
                    ORDER BY generated_at DESC
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
        if row and row[1] and row[1].date() >= today - timedelta(days=TAG_REFRESH_DAYS):
            payload = row[0]
            if isinstance(payload, dict) and isinstance(payload.get("items"), list):
                return payload["items"], False
    except psycopg.Error:
        # First run may not have the snapshots table yet, or the cache may not exist.
        return None, True
    return None, True


def stable_day_hash(day: date, section: str, url: str) -> str:
    raw = f"{day.isoformat()}|{section}|{url}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def diverse_select(
    videos: list[dict],
    limit: int,
    day: date,
    section: str,
    used_urls: set[str],
    order_mode: str,
) -> list[dict]:
    def sort_key(video):
        tie = stable_day_hash(day, section, video["url"])
        if order_mode == "popular":
            return (-video["views"], -video["quality_score"], tie)
        if order_mode == "discovery":
            return (-video["quality_score"], tie)
        return (-video["quality_score"], -video["views"], tie)

    selected = []
    channel_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    ordered = sorted(videos, key=sort_key)

    def try_add(video, channel_cap: int, tag_cap: int) -> bool:
        url = video["url"]
        if url in used_urls or len(selected) >= limit:
            return False
        channel = video["_channel_key"]
        tag = video["_tag_key"]
        if channel_counts.get(channel, 0) >= channel_cap:
            return False
        if tag_counts.get(tag, 0) >= tag_cap:
            return False
        used_urls.add(url)
        selected.append(video)
        channel_counts[channel] = channel_counts.get(channel, 0) + 1
        tag_counts[tag] = tag_counts.get(tag, 0) + 1
        return True

    # First pass prioritizes diversity; the next passes relax constraints only if needed.
    for channel_cap, tag_cap in ((2, 3), (3, 5), (4, 8)):
        for video in ordered:
            if len(selected) >= limit:
                break
            try_add(video, channel_cap, tag_cap)
        if len(selected) >= limit:
            break

    return [
        {key: value for key, value in video.items() if not key.startswith("_")}
        for video in selected
    ]


def cache_snapshot_payload(day: date, section: str, items: list, generated_at: str, source_counts: dict):
    return {
        "date": day.isoformat(),
        "section": section,
        "generated_at": generated_at,
        "rotation_window_days": 3,
        "source_counts": source_counts,
        "items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--candidate-limit", type=int, default=CANDIDATES_PER_QUERY)
    parser.add_argument("--init-schema-only", action="store_true")
    parser.add_argument("--skip-schema", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.days <= 7:
        parser.error("--days must be between 1 and 7")
    if args.candidate_limit < FEED_LIMIT:
        parser.error(f"--candidate-limit must be at least {FEED_LIMIT}")

    url1 = os.getenv("DATABASE_URL_1", "").strip()
    url2 = os.getenv("DATABASE_URL_2", "").strip()
    if not url1 or not url2:
        print("Set GitHub Actions secrets DATABASE_URL_1 and DATABASE_URL_2", file=sys.stderr)
        return 2
    if fingerprint(url1) == fingerprint(url2):
        print("DATABASE_URL_1 and DATABASE_URL_2 appear to point at the same database", file=sys.stderr)
        return 2

    try:
        if not args.skip_schema:
            with connect(url1, "schema") as conn:
                with conn.cursor() as cur:
                    cur.execute(CREATE_SNAPSHOT_TABLE_SQL)
                conn.commit()
        if args.init_schema_only:
            print("[schema] public.homepage_snapshots ready in DATABASE_URL_1", flush=True)
            return 0

        today = datetime.now(timezone.utc).date()
        generated_at = datetime.now(timezone.utc).isoformat()
        connection_urls = (url1, url2)
        candidates1 = get_candidates(url1, "db1-candidates", args.candidate_limit)
        candidates2 = get_candidates(url2, "db2-candidates", args.candidate_limit)
        merged: dict[str, dict] = {}
        for video in candidates1 + candidates2:
            old = merged.get(video["url"])
            if old is None or video["quality_score"] > old["quality_score"]:
                merged[video["url"]] = video
        candidates = list(merged.values())

        tag_catalog, refresh_tags = load_recent_tag_catalog(url1, today)
        if refresh_tags:
            tag_catalog = get_tag_catalog(connection_urls)
            print(f"[tags] refreshed canonical tag catalog: {len(tag_catalog)} tags", flush=True)
        else:
            print(f"[tags] using cached tag catalog: {len(tag_catalog or [])} tags", flush=True)
        tag_catalog = tag_catalog or []

        snapshots = []
        used_urls: set[str] = set()
        source_counts = {
            "database_1_candidates": len(candidates1),
            "database_2_candidates": len(candidates2),
            "merged_unique_candidates": len(candidates),
        }

        for offset in range(args.days):
            day = today + timedelta(days=offset)
            for section, mode in (
                ("featured", "featured"),
                ("popular", "popular"),
                ("discovery", "discovery"),
            ):
                items = diverse_select(
                    candidates, FEED_LIMIT, day, section, used_urls, mode
                )
                payload = cache_snapshot_payload(day, section, items, generated_at, source_counts)
                snapshots.append((day, section, payload, source_counts))
                print(
                    f"[snapshot] date={day.isoformat()} section={section} items={len(items)}",
                    flush=True,
                )

            tag_payload = cache_snapshot_payload(day, "tags", tag_catalog, generated_at, source_counts)
            snapshots.append((day, "tags", tag_payload, source_counts))

        if refresh_tags:
            canonical_payload = {
                "date": today.isoformat(),
                "section": "tag_catalog",
                "generated_at": generated_at,
                "items": tag_catalog,
            }
            snapshots.append((today, "tag_catalog", canonical_payload, source_counts))

        with connect(url1, "write-snapshots") as conn:
            with conn.cursor() as cur:
                cur.execute(CREATE_SNAPSHOT_TABLE_SQL)
                for day, section, payload, counts in snapshots:
                    cur.execute(
                        UPSERT_SNAPSHOT_SQL,
                        (day, section, Jsonb(payload), Jsonb(counts)),
                    )
            conn.commit()

        print(
            f"[done] candidate_pool={len(candidates):,}; days={args.days}; "
            f"snapshots_written={len(snapshots)}; destination=DATABASE_URL_1",
            flush=True,
        )
        return 0
    except Exception as exc:
        print(f"Homepage snapshot build failed: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
