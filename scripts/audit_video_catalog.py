#!/usr/bin/env python3
"""Read-only audit of the two video metadata databases."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from itertools import chain
from urllib.parse import urlparse

import psycopg

STOP_KEYS = {"video", "videos", "xvideo", "xvideos", "xvideoscom"}
BATCH_SIZE = 5000

SUMMARY_SQL = """
SELECT
    COUNT(*) AS total_rows,
    COUNT(*) FILTER (WHERE COALESCE(cardinality(tags), 0) > 0) AS rows_with_tags,
    COUNT(*) FILTER (WHERE COALESCE(cardinality(tags), 0) = 0) AS rows_without_tags,
    COALESCE(SUM(COALESCE(cardinality(tags), 0)), 0) AS tag_entries,
    COUNT(*) FILTER (WHERE tags_scraped_at IS NULL AND COALESCE(cardinality(tags), 0) = 0) AS tag_scrape_pending,
    COUNT(*) FILTER (WHERE NULLIF(BTRIM(title), '') IS NULL) AS blank_titles,
    COUNT(*) FILTER (WHERE NULLIF(BTRIM(img), '') IS NULL) AS blank_thumbnails,
    COUNT(*) FILTER (WHERE NULLIF(BTRIM(channel_name), '') IS NULL) AS blank_channel_names,
    pg_size_pretty(pg_table_size('public.videos')) AS table_size,
    pg_size_pretty(pg_indexes_size('public.videos')) AS index_size,
    pg_size_pretty(pg_total_relation_size('public.videos')) AS total_size
FROM public.videos
"""

TOP_TAGS_SQL = """
WITH expanded AS (
    SELECT
        tag,
        regexp_replace(lower(btrim(tag)), '[^[:alnum:]]', '', 'g') AS norm
    FROM public.videos
    CROSS JOIN LATERAL unnest(tags) AS tag
),
grouped AS (
    SELECT norm, MIN(tag) AS example_tag, COUNT(*) AS uses
    FROM expanded
    WHERE norm <> ''
    GROUP BY norm
)
SELECT norm, example_tag, uses
FROM grouped
WHERE norm <> ''
ORDER BY uses DESC
LIMIT 40
"""

INDEXES_SQL = """
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public' AND tablename = 'videos'
ORDER BY indexname
"""


def fingerprint(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    host = re.sub(r"-pooler(?=\.|$)", "", parsed.hostname or "").lower()
    return host, parsed.path


def bucket_for(url: str) -> int:
    return hashlib.sha256(url.encode("utf-8")).digest()[0] % 2


def connect(url: str, label: str):
    return psycopg.connect(
        url,
        connect_timeout=25,
        application_name=f"video-catalog-audit-{label}",
        prepare_threshold=None,
    )


def read_metrics(url: str, label: str) -> dict:
    with connect(url, label) as conn:
        with conn.cursor() as cur:
            cur.execute(SUMMARY_SQL)
            row = cur.fetchone()
            columns = [column.name for column in cur.description]
            summary = dict(zip(columns, row))

            cur.execute(TOP_TAGS_SQL, (list(STOP_KEYS), list(STOP_KEYS)))
            popular_tags = [
                {"normalized_tag": norm, "example": example, "uses": int(uses),
                 "generic": norm in STOP_KEYS}
                for norm, example, uses in cur.fetchall()
            ]
            summary["generic_tag_uses_in_top_tags"] = sum(
                item["uses"] for item in popular_tags if item["generic"]
            )
            summary["top_meaningful_tags"] = [
                item for item in popular_tags if not item["generic"]
            ][:25]
            summary["generic_tag_frequency"] = [
                item for item in popular_tags if item["generic"]
            ]
            cur.execute(INDEXES_SQL)
            summary["indexes"] = [
                {"name": name, "definition": definition}
                for name, definition in cur.fetchall()
            ]
    return summary


def iter_urls(connection_url: str, label: str):
    """Yield sorted URLs in bounded keyset batches, avoiding a 1.3M-item RAM list."""
    last_url = ""
    with connect(connection_url, f"{label}-url-scan") as conn:
        while True:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT url FROM public.videos WHERE url > %s ORDER BY url LIMIT %s",
                    (last_url, BATCH_SIZE),
                )
                batch = [row[0] for row in cur.fetchall()]
            if not batch:
                return
            for value in batch:
                yield value
            last_url = batch[-1]


def audit_partition(url1: str, url2: str) -> dict:
    """Confirm stable hash partitioning and detect duplicate URLs across sources."""
    a_iter = iter(iter_urls(url1, "db3"))
    b_iter = iter(iter_urls(url2, "db4"))
    a = next(a_iter, None)
    b = next(b_iter, None)
    count1 = count2 = duplicates = misplaced_db3 = misplaced_db4 = 0

    while a is not None or b is not None:
        if a is not None and (b is None or a < b):
            count1 += 1
            misplaced_db3 += int(bucket_for(a) != 0)
            a = next(a_iter, None)
        elif b is not None and (a is None or b < a):
            count2 += 1
            misplaced_db4 += int(bucket_for(b) != 1)
            b = next(b_iter, None)
        else:
            # Same URL exists in both databases.
            duplicates += 1
            count1 += 1
            count2 += 1
            misplaced_db3 += int(bucket_for(a) != 0)
            misplaced_db4 += int(bucket_for(b) != 1)
            a = next(a_iter, None)
            b = next(b_iter, None)

    return {
        "urls_seen_db3": count1,
        "urls_seen_db4": count2,
        "duplicate_urls_across_databases": duplicates,
        "urls_in_wrong_partition_db3": misplaced_db3,
        "urls_in_wrong_partition_db4": misplaced_db4,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-partition-audit",
        action="store_true",
        help="Skip the full sorted URL merge check; still audits counts, tags, sizes and indexes.",
    )
    args = parser.parse_args()

    url1 = os.getenv("DATABASE_URL_3", "").strip()
    url2 = os.getenv("DATABASE_URL_4", "").strip()
    if not url1 or not url2:
        print("Set GitHub Actions secrets DATABASE_URL_3 and DATABASE_URL_4", file=sys.stderr)
        return 2
    if fingerprint(url1) == fingerprint(url2):
        print("DATABASE_URL_3 and DATABASE_URL_4 appear to point at the same database", file=sys.stderr)
        return 2

    try:
        output = {
            "read_only": True,
            "generic_tag_keys_removed_by_future_ingestion": sorted(STOP_KEYS),
            "database_3": read_metrics(url1, "db3"),
            "database_4": read_metrics(url2, "db4"),
        }
        if not args.skip_partition_audit:
            output["partition_audit"] = audit_partition(url1, url2)
        print(json.dumps(output, indent=2, default=str), flush=True)
        return 0
    except Exception as exc:
        print(f"Catalog audit failed: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
