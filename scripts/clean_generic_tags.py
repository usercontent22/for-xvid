#!/usr/bin/env python3
"""Count or remove generic site-boilerplate tags from existing source databases.

Dry-run is the default. Applying changes requires --apply and an exact confirmation
phrase because this rewrites tag arrays for potentially millions of existing rows.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from urllib.parse import urlparse

import psycopg

try:
    from scripts.catalog_utils import GENERIC_TAG_KEYS
except ModuleNotFoundError:  # support direct execution: python scripts/clean_generic_tags.py
    from catalog_utils import GENERIC_TAG_KEYS

CONFIRM_PHRASE = "REMOVE GENERIC TAGS FROM ALL VIDEO ROWS"
BATCH_SIZE_DEFAULT = 5000

GENERIC_ROW_COUNT_SQL = """
SELECT COUNT(*)
FROM public.videos AS v
WHERE EXISTS (
    SELECT 1
    FROM unnest(v.tags) AS items(tag)
    WHERE regexp_replace(lower(btrim(tag)), '[^[:alnum:]]', '', 'g') = ANY(%s)
)
"""

NEXT_BATCH_SQL = """
SELECT v.url
FROM public.videos AS v
WHERE v.url > %s
  AND EXISTS (
      SELECT 1
      FROM unnest(v.tags) AS items(tag)
      WHERE regexp_replace(lower(btrim(tag)), '[^[:alnum:]]', '', 'g') = ANY(%s)
  )
ORDER BY v.url
LIMIT %s
"""

CLEAN_ROW_SQL = """
UPDATE public.videos AS v
SET tags = ARRAY(
    SELECT items.tag
    FROM unnest(v.tags) AS items(tag)
    WHERE regexp_replace(lower(btrim(items.tag)), '[^[:alnum:]]', '', 'g') <> ALL(%s)
)
WHERE v.url = %s
  AND EXISTS (
      SELECT 1
      FROM unnest(v.tags) AS items(tag)
      WHERE regexp_replace(lower(btrim(items.tag)), '[^[:alnum:]]', '', 'g') = ANY(%s)
  )
"""


def fingerprint(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    host = re.sub(r"-pooler(?=\.|$)", "", parsed.hostname or "").lower()
    return host, parsed.path


def connect(url: str, label: str):
    return psycopg.connect(
        url,
        connect_timeout=25,
        application_name=f"generic-tag-cleanup-{label}",
        prepare_threshold=None,
    )


def count_rows(connection_url: str, label: str, stop_keys: list[str]) -> int:
    with connect(connection_url, label) as conn:
        with conn.cursor() as cur:
            cur.execute(GENERIC_ROW_COUNT_SQL, (stop_keys,))
            row = cur.fetchone()
            return int(row[0])


def clean_database(
    connection_url: str, label: str, stop_keys: list[str], batch_size: int
) -> int:
    """Rewrite affected rows in small commits; progress is resumable by URL cursor."""
    last_url = ""
    updated = 0
    started = time.monotonic()
    while True:
        with connect(connection_url, f"{label}-read") as conn:
            with conn.cursor() as cur:
                cur.execute(NEXT_BATCH_SQL, (last_url, stop_keys, batch_size))
                rows = [row[0] for row in cur.fetchall()]
        if not rows:
            break

        # Keep the cursor advancing across all rows selected in this batch.
        last_url = rows[-1]
        with connect(connection_url, f"{label}-write") as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    CLEAN_ROW_SQL,
                    [(stop_keys, url, stop_keys) for url in rows],
                )
            conn.commit()
        updated += len(rows)
        print(
            f"[progress] {label}: cleaned={updated:,}; last_url={last_url}; "
            f"elapsed={time.monotonic() - started:.1f}s",
            flush=True,
        )
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually rewrite existing tag arrays")
    parser.add_argument("--confirm", default="", help=f"Required exact phrase: {CONFIRM_PHRASE!r}")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE_DEFAULT)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if args.apply and args.confirm != CONFIRM_PHRASE:
        parser.error(f"--apply requires --confirm {CONFIRM_PHRASE!r}")
    if not args.apply and args.confirm:
        parser.error("--confirm is only valid with --apply")

    url1 = os.getenv("DATABASE_URL_3", "").strip()
    url2 = os.getenv("DATABASE_URL_4", "").strip()
    if not url1 or not url2:
        print("Set GitHub Actions secrets DATABASE_URL_3 and DATABASE_URL_4", file=sys.stderr)
        return 2
    if fingerprint(url1) == fingerprint(url2):
        print("DATABASE_URL_3 and DATABASE_URL_4 appear to point at the same database", file=sys.stderr)
        return 2

    stop_keys = sorted(GENERIC_TAG_KEYS)
    try:
        before1 = count_rows(url1, "db3-count", stop_keys)
        before2 = count_rows(url2, "db4-count", stop_keys)
        print(
            f"[audit] generic-tag rows: db3={before1:,}; db4={before2:,}; "
            f"mode={'APPLY' if args.apply else 'DRY RUN'}",
            flush=True,
        )
        if not args.apply:
            print(
                "No data was changed. Review the count; to apply, use "
                f"--apply --confirm {CONFIRM_PHRASE!r}.",
                flush=True,
            )
            return 0

        done1 = clean_database(url1, "db3", stop_keys, args.batch_size)
        done2 = clean_database(url2, "db4", stop_keys, args.batch_size)
        print(
            f"[done] db3 cleaned={done1:,}; db4 cleaned={done2:,}; "
            f"dry_run=false",
            flush=True,
        )
        return 0
    except Exception as exc:
        print(f"Generic-tag cleanup failed: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
