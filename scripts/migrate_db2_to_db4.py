#!/usr/bin/env python3
"""Resumably copy public.videos from DATABASE_URL_2 into DATABASE_URL_4.

Rows are read in URL-primary-key order, transferred through PostgreSQL COPY into
a temporary staging table, and merged in one transaction per batch. Only the first
10 source tags per video are copied. Checkpoint updates commit atomically with rows.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from urllib.parse import urlparse

import psycopg

BATCH_SIZE_DEFAULT = 10_000
BATCH_SIZE_MAX = 50_000
MAX_TAGS_PER_ROW = 10
MIGRATION_NAME = "db2_to_db4_videos_v1"

VIDEO_COLUMNS = (
    "url, title, img, rating, rating_percentage, views, duration, "
    "duration_seconds, channel_name, tags"
)

CREATE_STATE_SQL = """
CREATE TABLE IF NOT EXISTS public.video_migration_state (
    migration_name TEXT PRIMARY KEY,
    last_url TEXT,
    rows_read BIGINT NOT NULL DEFAULT 0,
    rows_inserted BIGINT NOT NULL DEFAULT 0,
    completed BOOLEAN NOT NULL DEFAULT FALSE,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

CREATE_STAGE_SQL = """
CREATE TEMP TABLE IF NOT EXISTS _video_migration_stage (
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    img TEXT,
    rating TEXT,
    rating_percentage NUMERIC(5,2),
    views BIGINT,
    duration TEXT,
    duration_seconds INTEGER,
    channel_name TEXT,
    tags TEXT[] NOT NULL DEFAULT '{}'::text[]
) ON COMMIT DELETE ROWS
"""

INSERT_STAGE_SQL = f"""
INSERT INTO public.videos ({VIDEO_COLUMNS})
SELECT {VIDEO_COLUMNS}
FROM pg_temp._video_migration_stage
ORDER BY url
ON CONFLICT (url) DO NOTHING
"""

REQUIRED_COLUMNS = {
    "url", "title", "img", "rating", "rating_percentage",
    "views", "duration", "duration_seconds", "channel_name", "tags",
}


def fingerprint(connection_url: str) -> tuple[str, str]:
    """Normalize pooled/direct host spelling and identify a database by host + path."""
    parsed = urlparse(connection_url)
    host = re.sub(r"-pooler(?=\.|$)", "", parsed.hostname or "").lower()
    return host, parsed.path


def limit_tags(tags: object) -> list[str]:
    """Preserve source order while copying no more than ten array entries."""
    if not tags:
        return []
    if not isinstance(tags, (list, tuple)):
        raise TypeError("Source tags must be a PostgreSQL text array.")
    return list(tags[:MAX_TAGS_PER_ROW])


def connect(connection_url: str, label: str):
    return psycopg.connect(
        connection_url,
        connect_timeout=25,
        application_name=f"db2-to-db4-video-migration-{label}",
        prepare_threshold=None,
    )


def validate_destination_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'videos'
            """
        )
        columns = {row[0] for row in cur.fetchall()}
    missing = REQUIRED_COLUMNS - columns
    if missing:
        raise RuntimeError(
            "DATABASE_URL_4 public.videos is missing required columns: "
            + ", ".join(sorted(missing))
        )


def fetch_next_batch(source_conn, last_url: str | None, batch_size: int):
    """Use an index-friendly keyset query instead of OFFSET."""
    with source_conn.cursor() as cur:
        if last_url is None:
            cur.execute(
                f"SELECT {VIDEO_COLUMNS} FROM public.videos ORDER BY url LIMIT %s",
                (batch_size,),
            )
        else:
            cur.execute(
                f"SELECT {VIDEO_COLUMNS} FROM public.videos "
                "WHERE url > %s ORDER BY url LIMIT %s",
                (last_url, batch_size),
            )
        return cur.fetchall()


def initialize_state(target_conn) -> tuple[str | None, int, int, bool]:
    with target_conn.cursor() as cur:
        cur.execute(CREATE_STATE_SQL)
        cur.execute(CREATE_STAGE_SQL)
        cur.execute(
            """
            INSERT INTO public.video_migration_state (migration_name)
            VALUES (%s)
            ON CONFLICT (migration_name) DO NOTHING
            """,
            (MIGRATION_NAME,),
        )
        cur.execute(
            """
            SELECT last_url, rows_read, rows_inserted, completed
            FROM public.video_migration_state
            WHERE migration_name = %s
            """,
            (MIGRATION_NAME,),
        )
        state = cur.fetchone()
    target_conn.commit()
    if state is None:
        raise RuntimeError("Could not initialize migration checkpoint.")
    return state[0], int(state[1]), int(state[2]), bool(state[3])


def migrate(batch_size: int, max_batches: int) -> int:
    url1 = os.getenv("DATABASE_URL_2", "").strip()
    url3 = os.getenv("DATABASE_URL_4", "").strip()
    if not url1 or not url3:
        print(
            "Set GitHub Actions secrets DATABASE_URL_2 and DATABASE_URL_4.",
            file=sys.stderr,
        )
        return 2
    if fingerprint(url1) == fingerprint(url3):
        print(
            "DATABASE_URL_2 and DATABASE_URL_4 appear to point to the same database.",
            file=sys.stderr,
        )
        return 2

    started = time.monotonic()
    batches_this_run = 0

    try:
        with connect(url1, "source") as source_conn:
            # Keep source reads in short, independent transactions.
            source_conn.autocommit = True
            with connect(url3, "destination") as target_conn:
                validate_destination_schema(target_conn)
                last_url, rows_read, rows_inserted, completed = initialize_state(target_conn)

                if completed:
                    print(
                        f"[done] migration already complete; source_rows_seen={rows_read:,}; "
                        f"rows_inserted={rows_inserted:,}",
                        flush=True,
                    )
                    return 0

                print(
                    f"[start] batch_size={batch_size:,}; max_batches="
                    f"{max_batches or 'all remaining'}; resume_after={last_url or '(start)'}",
                    flush=True,
                )

                while True:
                    rows = fetch_next_batch(source_conn, last_url, batch_size)
                    if not rows:
                        with target_conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE public.video_migration_state
                                SET completed = TRUE, updated_at = now()
                                WHERE migration_name = %s
                                """,
                                (MIGRATION_NAME,),
                            )
                        target_conn.commit()
                        print(
                            f"[done] source_rows_seen={rows_read:,}; rows_inserted={rows_inserted:,}; "
                            f"elapsed={time.monotonic() - started:.1f}s",
                            flush=True,
                        )
                        return 0

                    # Explicitly bound each destination array regardless of source size.
                    payload = [
                        (*row[:9], limit_tags(row[9]))
                        for row in rows
                    ]
                    batch_last_url = rows[-1][0]

                    with target_conn.cursor() as cur:
                        with cur.copy(
                            f"COPY pg_temp._video_migration_stage ({VIDEO_COLUMNS}) FROM STDIN"
                        ) as copy:
                            for row in payload:
                                copy.write_row(row)

                        cur.execute(INSERT_STAGE_SQL)
                        inserted_this_batch = cur.rowcount
                        cur.execute(
                            """
                            UPDATE public.video_migration_state
                            SET last_url = %s,
                                rows_read = rows_read + %s,
                                rows_inserted = rows_inserted + %s,
                                updated_at = now()
                            WHERE migration_name = %s
                            """,
                            (batch_last_url, len(payload), inserted_this_batch, MIGRATION_NAME),
                        )
                    # Rows and checkpoint commit atomically: retries never skip an uncommitted batch.
                    target_conn.commit()

                    last_url = batch_last_url
                    rows_read += len(payload)
                    rows_inserted += inserted_this_batch
                    batches_this_run += 1
                    print(
                        f"[progress] batch={batches_this_run:,}; rows_read={rows_read:,}; "
                        f"inserted_total={rows_inserted:,}; batch_inserted={inserted_this_batch:,}; "
                        f"tags_max={MAX_TAGS_PER_ROW}; elapsed={time.monotonic() - started:.1f}s",
                        flush=True,
                    )

                    if max_batches and batches_this_run >= max_batches:
                        print(
                            f"[paused] checkpoint saved at last_url={last_url}; "
                            "rerun the workflow with max_batches=0 to finish.",
                            flush=True,
                        )
                        return 0
    except Exception as exc:
        # Do not print connection URLs or credentials.
        print(f"Database migration failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE_DEFAULT)
    parser.add_argument(
        "--max-batches",
        type=int,
        default=0,
        help="Stop after this many batches (0 means process all remaining rows).",
    )
    args = parser.parse_args()
    if not 1 <= args.batch_size <= BATCH_SIZE_MAX:
        parser.error(f"--batch-size must be between 1 and {BATCH_SIZE_MAX}")
    if args.max_batches < 0:
        parser.error("--max-batches cannot be negative")
    return migrate(args.batch_size, args.max_batches)


if __name__ == "__main__":
    raise SystemExit(main())
