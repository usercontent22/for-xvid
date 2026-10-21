#!/usr/bin/env python3
"""Resumable channel-video metadata collector for GitHub Actions."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from urllib.parse import urljoin, urlparse

import psycopg
import requests

BASE = "https://www.xvideos.com"
HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE,
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/155.0.0.0 Safari/537.36",
}
_tls = threading.local()

VIDEOS_SQL = """
CREATE TABLE IF NOT EXISTS videos (
    url TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    img TEXT,
    rating TEXT,
    rating_percentage NUMERIC(5,2),
    views BIGINT,
    duration TEXT,
    duration_seconds INTEGER,
    channel_name TEXT,
    tags TEXT[] NOT NULL DEFAULT '{}'::text[]
)
"""
STATE_SQL = """
CREATE TABLE IF NOT EXISTS video_scrape_channel_state (
    channel_slug TEXT PRIMARY KEY, nb_videos BIGINT NOT NULL, nb_per_page INTEGER NOT NULL
)
"""
PAGES_SQL = """
CREATE TABLE IF NOT EXISTS video_scrape_completed_pages (
    channel_slug TEXT NOT NULL, page INTEGER NOT NULL, video_count INTEGER NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (channel_slug, page)
)
"""
UPSERT_SQL = """
INSERT INTO videos
(url, title, img, rating, rating_percentage, views, duration, duration_seconds, channel_name, tags)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (url) DO UPDATE SET
 title = COALESCE(NULLIF(EXCLUDED.title, ''), videos.title),
 img = COALESCE(NULLIF(EXCLUDED.img, ''), videos.img),
 rating = COALESCE(EXCLUDED.rating, videos.rating),
 rating_percentage = COALESCE(EXCLUDED.rating_percentage, videos.rating_percentage),
 views = COALESCE(EXCLUDED.views, videos.views),
 duration = COALESCE(EXCLUDED.duration, videos.duration),
 duration_seconds = COALESCE(EXCLUDED.duration_seconds, videos.duration_seconds),
 channel_name = COALESCE(NULLIF(EXCLUDED.channel_name, ''), videos.channel_name),
 tags = CASE WHEN cardinality(EXCLUDED.tags) > 0 THEN EXCLUDED.tags ELSE videos.tags END
"""


def http_session():
    if not hasattr(_tls, "session"):
        _tls.session = requests.Session()
        _tls.session.headers.update(HEADERS)
    return _tls.session


def markdown_target(value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    match = re.fullmatch(r"\[([^\]]+)\]\(([^)]+)\)", value)
    return match.group(2).strip() if match else value


def channel_slug(href):
    href = markdown_target(href)
    if not href:
        return None
    parts = [p for p in urlparse(urljoin(BASE, href)).path.strip("/").split("/") if p]
    if not parts:
        return None
    if parts[0].lower() in {"channels", "profiles", "pornstars", "models", "users", "creators", "uploader"}:
        return parts[1] if len(parts) > 1 else None
    return parts[0] if len(parts) == 1 else None


def clean_url(value):
    value = markdown_target(value)
    if not value:
        return None
    path = urlparse(urljoin(BASE, value)).path or ""
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 4 and parts[:2] == ["prof-video-click", "upload"]:
        path = "/" + "/".join(parts[3:])
    elif not path.startswith("/"):
        path = "/" + path
    path = re.sub(r"/{2,}", "/", path)
    return path if path and path != "/" else None


def clean_img(value):
    value = markdown_target(value)
    if not value:
        return None
    parsed = urlparse(urljoin(BASE, value))
    if not parsed.path:
        return None
    path = parsed.path.lstrip("/")
    host = (parsed.hostname or "").lower()
    return ("g/" if host.startswith("thumbs-gcore.") else "/") + path


def integer_views(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.match(r"^\s*([\d,.]+)\s*([kmb]?)\s*$", str(value), re.I)
    if not match:
        return None
    multiplier = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
    return int(float(match.group(1).replace(",", "")) * multiplier[match.group(2).lower()])


def duration_seconds(value):
    if value is None:
        return None
    value = str(value).strip().lower()
    pieces = re.findall(r"(\d+)\s*(h(?:ours?)?|min(?:utes?)?|m|sec(?:onds?)?|s)\b", value)
    if pieces:
        return sum(int(n) * (3600 if u.startswith("h") else 60 if u.startswith("m") else 1) for n, u in pieces)
    if re.fullmatch(r"\d+(?::\d{1,2}){1,2}", value):
        total = 0
        for part in value.split(":"):
            total = total * 60 + int(part)
        return total
    return None


def rating_percentage(value):
    try:
        return float(str(value).strip().rstrip("%")) if value is not None else None
    except ValueError:
        return None


def clean_tags(value):
    if isinstance(value, dict):
        items = list(value)
    elif isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = re.split(r"[,|]", value)
    else:
        items = []
    return list(dict.fromkeys(str(v).strip() for v in items if str(v).strip()))


def normalize(item, fallback_slug):
    if not isinstance(item, dict):
        return None
    url = clean_url(item.get("u") or item.get("url"))
    if not url:
        return None
    rating = item.get("r", item.get("rating"))
    duration = item.get("d", item.get("duration"))
    return {
        "url": url,
        "title": str(item.get("tf") or item.get("title") or item.get("t") or "").strip(),
        "img": clean_img(item.get("i") or item.get("img") or item.get("thumbnail")),
        "rating": str(rating) if rating is not None else None,
        "rating_percentage": rating_percentage(rating),
        "views": integer_views(item.get("n", item.get("views"))),
        "duration": str(duration) if duration is not None else None,
        "duration_seconds": duration_seconds(duration),
        "channel_name": str(item.get("pn") or item.get("channel_name") or item.get("p") or fallback_slug).strip(),
        "tags": clean_tags(item.get("tags", item.get("tag"))),
    }


def video_items(data):
    if not isinstance(data, dict):
        raise ValueError("API response is not a JSON object")
    raw = data.get("videos")
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        keys = [k for k in raw if str(k).isdigit()]
        if len(keys) != len(raw):
            raise ValueError("Unexpected non-numeric keys in videos object")
        items = [raw[k] for k in sorted(keys, key=lambda k: int(k))]
    else:
        raise ValueError("Unexpected videos payload type")
    if any(not isinstance(v, dict) for v in items):
        raise ValueError("A video entry is not an object")
    return items


def request_page(slug, page, retries, timeout, delay):
    url = f"{BASE}/channels/{requests.utils.quote(slug, safe='')}/videos/best/{page}"
    for attempt in range(retries + 1):
        try:
            if delay:
                time.sleep(delay + random.uniform(0, delay))
            response = http_session().post(
                url, headers={"Referer": f"{BASE}/{requests.utils.quote(slug, safe='')}"}, timeout=timeout
            )
            if response.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                header = response.headers.get("Retry-After", "")
                try:
                    pause = min(float(header), 90) if header else min(2 ** attempt * 2, 30)
                except ValueError:
                    pause = min(2 ** attempt * 2, 30)
                time.sleep(pause + random.random())
                continue
            response.raise_for_status()
            data = response.json()
            data["videos"] = video_items(data)
            return data
        except (requests.RequestException, ValueError) as exc:
            if attempt == retries:
                raise RuntimeError(f"channel={slug} page={page}: {exc}") from exc
            time.sleep(min(2 ** attempt * 2, 30) + random.random())
    raise RuntimeError(f"channel={slug} page={page}: retry loop exhausted")


def load_channels(path, minimum):
    content = Path(path).read_text(encoding="utf-8-sig")
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        if not content.lstrip().startswith("["):
            raise ValueError("channels.json must be a JSON array") from exc
        decoder, pos, recovered = json.JSONDecoder(), len(content) - len(content.lstrip()) + 1, []
        while pos < len(content):
            while pos < len(content) and content[pos] in " \r\n\t,":
                pos += 1
            if pos >= len(content) or content[pos] == "]":
                break
            try:
                row, end = decoder.raw_decode(content, pos)
            except json.JSONDecodeError:
                break
            if not isinstance(row, dict):
                raise ValueError(f"Invalid channel record at offset {pos}") from exc
            recovered.append(row)
            pos = end
            while pos < len(content) and content[pos].isspace():
                pos += 1
            if pos < len(content) and content[pos] not in ",]":
                break
        if not recovered:
            raise ValueError("No complete channel rows could be recovered") from exc
        print(f"[recovery] loaded {len(recovered):,} complete channels from truncated input")
        raw = recovered
    if not isinstance(raw, list):
        raise ValueError("channels.json must be a JSON array")
    selected = set()
    for row in raw:
        if not isinstance(row, dict):
            continue
        slug = channel_slug(row.get("href") or row.get("url") or "")
        count = row.get("total_videos")
        if count is None:
            count = row.get("video_count")
        if not slug:
            continue
        # A null/missing inventory count is unknown, not zero: include it for
        # an API probe and decide eligibility from that channel's real count.
        if count is None or (isinstance(count, str) and count.strip().lower() in {"", "null", "none"}):
            selected.add(slug)
            continue
        if isinstance(count, bool):
            continue
        try:
            count = int(str(count).replace(",", "").strip())
        except (ValueError, TypeError):
            continue
        if count >= minimum:
            selected.add(slug)
    return sorted(selected)


def reported_video_count(data):
    """Return the API's true count, falling back to this page's item count."""
    value = data.get("nb_videos")
    if value is not None and str(value).strip() != "":
        try:
            return max(0, int(str(value).replace(",", "").strip()))
        except (TypeError, ValueError):
            pass
    items = data.get("videos")
    return len(items) if isinstance(items, (list, dict)) else 0


def bucket_for(url):
    return hashlib.sha256(url.encode("utf-8")).digest()[0] % 2


def db_fingerprint(url):
    parsed = urlparse(url)
    return re.sub(r"-pooler(?=\.|$)", "", parsed.hostname or "").lower(), parsed.path


def connect_db(url, app_name):
    return psycopg.connect(url, connect_timeout=20, application_name=app_name, prepare_threshold=None)


def init_schema(conn1, conn2):
    for conn in (conn1, conn2):
        with conn.cursor() as cur:
            cur.execute(VIDEOS_SQL)
        conn.commit()
    with conn1.cursor() as cur:
        cur.execute(STATE_SQL)
        cur.execute(PAGES_SQL)
    conn1.commit()
    required = {"url", "title", "img", "rating", "rating_percentage", "views", "duration",
                "duration_seconds", "channel_name", "tags"}
    for name, conn in (("DATABASE_URL_1", conn1), ("DATABASE_URL_2", conn2)):
        with conn.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = current_schema() AND table_name = 'videos'")
            missing = required - {row[0] for row in cur.fetchall()}
        if missing:
            raise RuntimeError(f"{name} public.videos missing columns: {', '.join(sorted(missing))}")
    print("[schema] videos table ready in both databases; checkpoints are in database 1", flush=True)


class Writer:
    def __init__(self, conn1, conn2):
        self.conns = (conn1, conn2)
        self.lock = threading.Lock()

    def save(self, slug, page, raw_items, state=None):
        rows = [normalize(item, slug) for item in raw_items]
        buckets = ([], [])
        for item in (r for r in rows if r):
            values = (item["url"], item["title"], item["img"], item["rating"],
                      item["rating_percentage"], item["views"], item["duration"],
                      item["duration_seconds"], item["channel_name"], item["tags"])
            buckets[bucket_for(item["url"])].append(values)
        with self.lock:
            try:
                for conn, batch in zip(self.conns, buckets):
                    if batch:
                        with conn.cursor() as cur:
                            cur.executemany(UPSERT_SQL, batch)
                        conn.commit()
                # Mark complete only after writes to both databases have committed.
                with self.conns[0].cursor() as cur:
                    if state:
                        cur.execute(
                            "INSERT INTO video_scrape_channel_state VALUES (%s,%s,%s) "
                            "ON CONFLICT (channel_slug) DO UPDATE SET "
                            "nb_videos=EXCLUDED.nb_videos, nb_per_page=EXCLUDED.nb_per_page",
                            (slug, state[0], state[1]),
                        )
                    cur.execute(
                        "INSERT INTO video_scrape_completed_pages (channel_slug,page,video_count) "
                        "VALUES (%s,%s,%s) ON CONFLICT (channel_slug,page) DO UPDATE SET "
                        "video_count=EXCLUDED.video_count, completed_at=now()",
                        (slug, page, sum(1 for r in rows if r)),
                    )
                self.conns[0].commit()
            except Exception:
                for conn in self.conns:
                    conn.rollback()
                raise
        return sum(1 for r in rows if r)


def bounded_map(executor, items, fn, limit):
    iterator, pending = iter(items), {}
    exhausted = False
    while pending or not exhausted:
        while not exhausted and len(pending) < limit:
            try:
                item = next(iterator)
            except StopIteration:
                exhausted = True
                break
            pending[executor.submit(fn, item)] = item
        if not pending:
            break
        done, _ = wait(pending, return_when=FIRST_COMPLETED)
        for future in done:
            item = pending.pop(future)
            try:
                yield item, future.result(), None
            except Exception as exc:
                yield item, None, exc


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--channels", default="channels.json")
    ap.add_argument("--shard-index", type=int, default=0)
    ap.add_argument("--shard-count", type=int, default=20)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--timeout", type=int, default=25)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--min-videos", type=int, default=10)
    ap.add_argument("--max-channels", type=int, default=0)
    ap.add_argument("--max-pages", type=int, default=0)
    ap.add_argument("--refresh-recent-pages", type=int, default=2)
    ap.add_argument("--init-schema-only", action="store_true")
    ap.add_argument("--skip-schema", action="store_true")
    ap.add_argument("--failed-output", default="failed_pages.json")
    args = ap.parse_args()
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        ap.error("shard-index must be within shard-count")
    if args.workers < 1 or args.delay < 0 or args.timeout < 1 or args.retries < 0:
        ap.error("workers/timeout must be positive; delay/retries must be non-negative")
    url1, url2 = os.getenv("DATABASE_URL_1", "").strip(), os.getenv("DATABASE_URL_2", "").strip()
    if not url1 or not url2:
        raise SystemExit("Set GitHub Actions secrets DATABASE_URL_1 and DATABASE_URL_2")
    if db_fingerprint(url1) == db_fingerprint(url2):
        raise SystemExit("DATABASE_URL_1 and DATABASE_URL_2 appear to point at the same database")
    conn1 = conn2 = None
    failures = []
    try:
        conn1 = connect_db(url1, f"video-collector-{args.shard_index}-db1")
        conn2 = connect_db(url2, f"video-collector-{args.shard_index}-db2")
        if not args.skip_schema:
            init_schema(conn1, conn2)
        if args.init_schema_only:
            return 0
        channels = load_channels(args.channels, args.min_videos)
        if args.max_channels:
            channels = channels[:args.max_channels]
        channels = [slug for slug in channels
                    if int(hashlib.sha256(slug.encode()).hexdigest()[:8], 16) % args.shard_count == args.shard_index]
        print(f"[shard {args.shard_index}/{args.shard_count}] channels={len(channels):,}; workers={args.workers}", flush=True)
        if not channels:
            Path(args.failed_output).write_text("[]\n", encoding="utf-8")
            return 0
        with conn1.cursor() as cur:
            cur.execute("SELECT channel_slug,page FROM video_scrape_completed_pages WHERE channel_slug = ANY(%s)", (channels,))
            completed = {(s, int(p)) for s, p in cur.fetchall()}
            cur.execute("SELECT channel_slug,nb_videos,nb_per_page FROM video_scrape_channel_state WHERE channel_slug = ANY(%s)", (channels,))
            states = {s: (int(n), max(1, int(size))) for s, n, size in cur.fetchall()}
        writer = Writer(conn1, conn2)
        probes = [slug for slug in channels if args.refresh_recent_pages > 0 or slug not in states or (slug, 0) not in completed]

        def probe(slug):
            data = request_page(slug, 0, args.retries, args.timeout, args.delay)
            items = data["videos"]
            total = reported_video_count(data)
            per_page = max(1, int(data.get("nb_per_page") or len(items) or 36))
            eligible = total >= args.min_videos
            # Don't ingest page zero for a channel whose actual API total is
            # below the threshold. Still checkpoint the probe so its count is
            # known and it can be reconsidered on refresh runs.
            saved = writer.save(slug, 0, items if eligible else [], (total, per_page))
            if not eligible:
                print(f"[skip] channel={slug}: API reports {total} videos (< {args.min_videos})", flush=True)
            return slug, total, per_page, saved

        page_count = row_count = 0
        with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="channel") as pool:
            for slug, result, error in bounded_map(pool, probes, probe, max(2, args.workers * 3)):
                if error:
                    failures.append({"channel_slug": slug, "page": 0, "error": str(error)})
                    print(f"[error] channel={slug} page=0: {error}", flush=True)
                    continue
                s, total, per_page, saved = result
                states[s] = (total, per_page)
                completed.add((s, 0))
                page_count += 1
                row_count += saved

        tasks = []
        for slug in channels:
            if slug not in states:
                continue
            total, per_page = states[slug]
            pages = max(1, math.ceil(total / per_page))
            if args.max_pages:
                pages = min(pages, args.max_pages)
            for page in range(1, pages):
                if page < args.refresh_recent_pages or (slug, page) not in completed:
                    tasks.append((slug, page))

        def fetch_save(task):
            slug, page = task
            data = request_page(slug, page, args.retries, args.timeout, args.delay)
            saved = writer.save(slug, page, data["videos"])
            return saved

        print(f"[pages] to fetch={len(tasks):,}; already complete={len(completed):,}", flush=True)
        with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="page") as pool:
            for task, saved, error in bounded_map(pool, tasks, fetch_save, max(2, args.workers * 4)):
                slug, page = task
                if error:
                    failures.append({"channel_slug": slug, "page": page, "error": str(error)})
                    print(f"[error] channel={slug} page={page}: {error}", flush=True)
                    continue
                page_count += 1
                row_count += saved
                if page_count % 100 == 0:
                    print(f"[progress] pages={page_count:,}; rows={row_count:,}; failures={len(failures):,}", flush=True)
        Path(args.failed_output).write_text(json.dumps(failures, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[done] pages processed={page_count:,}; normalized rows={row_count:,}; failures={len(failures):,}", flush=True)
        return 1 if failures else 0
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr, flush=True)
        Path(args.failed_output).write_text(json.dumps([{"fatal_error": str(exc)}], indent=2) + "\n", encoding="utf-8")
        return 1
    finally:
        for conn in (conn1, conn2):
            if conn is not None:
                conn.close()
