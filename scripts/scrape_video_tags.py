#!/usr/bin/env python3
"""Extract HTML meta keywords for videos that have not been tag-scraped yet."""
from __future__ import annotations

import argparse
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse, urlunparse

import psycopg
import requests
from bs4 import BeautifulSoup

BASE = os.getenv("VIDEO_BASE_URL", "https://www.xvideos.com").rstrip("/")
HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
    ),
    "Referer": BASE + "/",
}
_thread_local = threading.local()

ADD_STATUS_COLUMN_SQL = (
    "ALTER TABLE public.videos "
    "ADD COLUMN IF NOT EXISTS tags_scraped_at TIMESTAMPTZ"
)
PENDING_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS videos_tags_pending_idx "
    "ON public.videos (url) WHERE tags_scraped_at IS NULL"
)
SELECT_PENDING_SQL = """
SELECT url, tags
FROM public.videos
WHERE tags_scraped_at IS NULL
  AND url > %s
  AND mod(abs(hashtext(url)::bigint), %s) = %s
ORDER BY url
LIMIT %s
"""
UPDATE_TAGS_SQL = """
UPDATE public.videos
SET tags = CASE
        WHEN cardinality(tags) = 0 THEN %s::text[]
        ELSE tags
    END,
    tags_scraped_at = now()
WHERE url = %s
  AND tags_scraped_at IS NULL
"""


def get_session():
    if not hasattr(_thread_local, "session"):
        session = requests.Session()
        session.headers.update(HEADERS)
        _thread_local.session = session
    return _thread_local.session


def normalize_video_url(value):
    value = (value or "").strip()
    if not value:
        raise ValueError("Video URL is empty")
    return urljoin(BASE + "/", value)


def build_request_url(value):
    """Convert /VIDEO_ID/slug to /video.VIDEO_ID/slug for the HTML request only."""
    normalized = normalize_video_url(value)
    parsed = urlparse(normalized)
    base_host = (urlparse(BASE).hostname or "").lower().removeprefix("www.")
    target_host = (parsed.hostname or "").lower().removeprefix("www.")
    if target_host != base_host:
        return normalized

    segments = parsed.path.split("/")
    if len(segments) > 1 and segments[1] and not segments[1].lower().startswith("video."):
        segments[1] = "video." + segments[1]
        parsed = parsed._replace(path="/".join(segments))
    return urlunparse(parsed)


def extract_tags(html):
    """Collect comma-separated values from the same two meta selectors as the test script."""
    soup = BeautifulSoup(html, "html.parser")
    tags = set()
    for meta in soup.select('meta[property="video:tag"], meta[name="keywords"]'):
        content = (meta.get("content") or "").strip()
        if content:
            tags.update(tag.strip() for tag in content.split(",") if tag.strip())
    return sorted(tags, key=str.casefold)


def request_tags(video_url, retries=4, timeout=30, delay=1.0):
    target = build_request_url(video_url)
    last_error = None
    for attempt in range(retries + 1):
        try:
            if delay:
                time.sleep(delay + random.uniform(0, delay))
            response = get_session().get(target, timeout=timeout)
            if response.status_code in (408, 425, 429, 500, 502, 503, 504) and attempt < retries:
                retry_after = response.headers.get("Retry-After", "")
                try:
                    backoff = min(float(retry_after), 90) if retry_after else min(2 ** attempt * 2, 30)
                except ValueError:
                    backoff = min(2 ** attempt * 2, 30)
                time.sleep(backoff + random.random())
                continue
            response.raise_for_status()
            return extract_tags(response.text)
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(2 ** attempt * 2, 30) + random.random())
    raise RuntimeError(f"{target}: {last_error}") from last_error


def database_fingerprint(connection_url):
    parsed = urlparse(connection_url)
    hostname = re.sub(r"-pooler(?=\.|$)", "", parsed.hostname or "").lower()
    return hostname, parsed.path


def connect_db(connection_url, application_name):
    return psycopg.connect(
        connection_url,
        connect_timeout=25,
        application_name=application_name,
        prepare_threshold=None,
    )


def init_schema(connection_urls):
    for index, connection_url in enumerate(connection_urls, start=1):
        conn = None
        try:
            print(f"[schema] connecting to DATABASE_URL_{index}", flush=True)
            conn = connect_db(connection_url, f"video-tag-schema-{index}")
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name='videos'"
                )
                if cur.fetchone() is None:
                    raise RuntimeError(
                        f"DATABASE_URL_{index} has no public.videos table. "
                        "Run the video metadata workflow first."
                    )
                cur.execute(ADD_STATUS_COLUMN_SQL)
                cur.execute(PENDING_INDEX_SQL)
            conn.commit()
            print(f"[schema] DATABASE_URL_{index}: tags_scraped_at ready", flush=True)
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if conn is not None:
                conn.close()


def process_database(
    connection_url,
    database_label,
    shard_index,
    shard_count,
    workers,
    delay,
    timeout,
    retries,
    batch_size,
    max_rows,
):
    conn = connect_db(connection_url, f"video-tags-{database_label}-shard-{shard_index}")
    processed = 0
    fetched = 0
    tags_found = 0
    already_tagged = 0
    failures = 0
    cursor_url = ""
    started = time.monotonic()
    try:
        print(
            f"[{database_label} shard {shard_index}/{shard_count}] starting "
            f"(workers={workers}, batch_size={batch_size}, max_rows={max_rows or 'all'})",
            flush=True,
        )
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tag-http") as pool:
            while True:
                limit = batch_size
                if max_rows:
                    remaining = max_rows - processed
                    if remaining <= 0:
                        break
                    limit = min(limit, remaining)
                with conn.cursor() as cur:
                    cur.execute(
                        SELECT_PENDING_SQL,
                        (cursor_url, shard_count, shard_index, limit),
                    )
                    rows = cur.fetchall()
                if not rows:
                    break

                cursor_url = rows[-1][0]
                updates = []
                futures = {}
                for video_url, existing_tags in rows:
                    if existing_tags:
                        # Preserve tags written elsewhere, but don't re-fetch this URL forever.
                        updates.append(([], video_url))
                        already_tagged += 1
                    else:
                        futures[pool.submit(request_tags, video_url, retries, timeout, delay)] = video_url

                for future in as_completed(futures):
                    video_url = futures[future]
                    try:
                        tags = future.result()
                        updates.append((tags, video_url))
                        tags_found += len(tags)
                        fetched += 1
                    except Exception as exc:
                        failures += 1
                        print(f"[error] {database_label} url={video_url}: {exc}", file=sys.stderr, flush=True)

                try:
                    if updates:
                        with conn.cursor() as cur:
                            cur.executemany(UPDATE_TAGS_SQL, updates)
                        conn.commit()
                except Exception:
                    conn.rollback()
                    raise

                processed += len(rows)
                elapsed = max(time.monotonic() - started, 0.001)
                print(
                    f"[progress] {database_label} shard={shard_index}: "
                    f"scanned={processed:,}; fetched={fetched:,}; "
                    f"tags_found={tags_found:,}; already_tagged={already_tagged:,}; "
                    f"request_failures={failures:,}; rate={processed / elapsed:.1f} rows/s",
                    flush=True,
                )
        elapsed = max(time.monotonic() - started, 0.001)
        print(
            f"[done] {database_label} shard={shard_index}: scanned={processed:,}; "
            f"pages_fetched={fetched:,}; tags_found={tags_found:,}; "
            f"already_tagged={already_tagged:,}; request_failures={failures:,}; "
            f"elapsed={elapsed:.1f}s",
            flush=True,
        )
        return failures
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=20)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--max-rows-per-db", type=int, default=0)
    parser.add_argument("--init-schema-only", action="store_true")
    parser.add_argument("--skip-schema", action="store_true")
    args = parser.parse_args()

    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        parser.error("shard-index must be within shard-count")
    if min(args.workers, args.timeout, args.batch_size) < 1:
        parser.error("workers, timeout and batch-size must be positive")
    if args.delay < 0 or args.retries < 0 or args.max_rows_per_db < 0:
        parser.error("delay, retries and max-rows-per-db must be non-negative")

    url1 = os.getenv("DATABASE_URL_1", "").strip()
    url2 = os.getenv("DATABASE_URL_2", "").strip()
    if not url1 or not url2:
        raise SystemExit("Set GitHub Actions secrets DATABASE_URL_1 and DATABASE_URL_2")
    if database_fingerprint(url1) == database_fingerprint(url2):
        raise SystemExit("DATABASE_URL_1 and DATABASE_URL_2 appear to point at the same database")

    connection_urls = (url1, url2)
    failures = 0
    try:
        if not args.skip_schema:
            init_schema(connection_urls)
        if args.init_schema_only:
            return 0

        for db_index, connection_url in enumerate(connection_urls, start=1):
            failures += process_database(
                connection_url=connection_url,
                database_label=f"db{db_index}",
                shard_index=args.shard_index,
                shard_count=args.shard_count,
                workers=args.workers,
                delay=args.delay,
                timeout=args.timeout,
                retries=args.retries,
                batch_size=args.batch_size,
                max_rows=args.max_rows_per_db,
            )
        print(
            f"[shard {args.shard_index}/{args.shard_count}] finished both databases; "
            f"request_failures={failures:,}",
            flush=True,
        )
        return 1 if failures else 0
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
