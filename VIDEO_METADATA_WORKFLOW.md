# Video metadata collection workflow

## Configure secrets

In **Settings → Secrets and variables → Actions**, create:

- `DATABASE_URL_1`: PostgreSQL connection string for database 1.
- `DATABASE_URL_2`: PostgreSQL connection string for a *different* database 2.

Prefer Neon's pooled connection strings for application workloads. Never commit connection strings to this public repository. The collector refuses URLs that appear to identify the same database.

## What it writes

The workflow reads `channels.json` and creates `public.videos` in both databases with:

Channel selection treats a known inventory count of 10 or above as eligible and skips known counts below 10. If `total_videos` is null or blank, the channel is still probed; the API's `nb_videos` count determines eligibility (falling back to the returned page item count if the API omits its total). If the API count is below 10, the probe is checkpointed but its video rows are not ingested. This avoids silently losing channels whose inventory count is unknown.

`url` (primary key), `title`, `img`, `rating`, `rating_percentage`, `views`, `duration`, `duration_seconds`, `channel_name`, and `tags`.

- Video URL: strips `/prof-video-click/upload/<channel>/`; e.g. `/prof-video-click/upload/channel/hvtebdd/shy_misty_in` becomes `/hvtebdd/shy_misty_in`.
- Thumbnail: removes the domain. Ordinary thumbnails become `/path/to/image.jpg`; thumbnails from `thumbs-gcore.xvideos-cdn.com` become `g/path/to/image.jpg`.
- Views: values such as `907.6k` become integer `907600`.
- Duration: keeps source text and adds `duration_seconds` where it can parse a duration.
- Rating: keeps the source string and stores a numeric percentage separately.

Rows are assigned using `SHA-256(url) first byte modulo 2`, which keeps URLs on the same database across reruns while balancing both projects approximately evenly. This prevents a duplicate URL from living in both databases due to different crawler shards.

Two small checkpoint tables live only in database 1: `video_scrape_channel_state` and `video_scrape_completed_pages`. Pages are marked complete after both database writes. If a run stops between database commits, a retry safely upserts the page again. Non-empty existing tags are not overwritten by an empty tag list from this collector.

The supplied API sample does not include a tags field. New rows therefore start with an empty tags array. Categories are deliberately out of scope; no category data or category tables are created.

## Run it

Go to **Actions → Collect channel video metadata → Run workflow**. For the initial smoke test, use `max_channels=20`, `max_pages=2`, and `workers_per_shard=1`; inspect both databases before a full run. For the full crawl, set both limits to `0`.

The matrix has 20 stable shards. The default is one request worker per shard and a one-second base pause plus a random pause. Raise parallelism only gradually, while watching for 429s and respecting the source website's terms and rate limits. Temporary 429/5xx responses use exponential backoff. Each runner job has a six-hour timeout. Public-repository standard runners are free, but the account still has a concurrency ceiling.

The workflow runs daily and refreshes the first two pages of each channel to capture new uploads. Completed older pages are skipped. Failed pages are left uncheckpointed and retried on a later run. Errors and progress are printed directly in the GitHub Actions logs; no workflow artifacts are created.

## Video tag scraping workflow

The separate **Scrape video tags** workflow visits each pending video URL and parses these HTML meta elements:

- `meta[property="video:tag"]`
- `meta[name="keywords"]`

Each value is split on commas, trimmed, deduplicated, and stored as a sorted `text[]` in `public.videos.tags`. The scraper does not infer categories.

Before each HTML request, a stored path like `/aalcci2892/naughty_babes_aj_applegate_and_brookl` is requested as `/video.aalcci2892/naughty_babes_aj_applegate_and_brookl`. This rewrite affects the request URL only; the database's original `url` value is not modified. Already-prefixed URLs are not prefixed twice.

The tag workflow runs automatically after a successful **Collect channel video metadata** workflow on `main`, and it can also be started manually from **Actions → Scrape video tags**. It uses 20 matrix shards and four HTTP workers per shard by default (up to 80 concurrent source requests if all jobs are running). Requests have timeouts, retries, exponential backoff, and a configurable delay. Start with a manual test using `max_rows_per_db=5`; use `0` for the full pending backlog.

For resumability, the tag workflow adds `tags_scraped_at` to `public.videos` and a partial index for rows that still need processing. The timestamp is set even when the page has no keyword tags, so such pages are not fetched again on every run. Existing non-empty tags are preserved. Failed requests are logged and left pending so a later run can retry them.

The workflow does not upload artifacts or generate report files; progress and failures are available directly in the GitHub Actions logs.



## Stage 2: catalog, search, and homepage optimization

### Read-only catalog audit

Run **Actions → Stage 2 catalog audit** to inspect both source databases. It reports row counts, tag coverage, empty key fields, table/index sizes, common tags and (unless skipped) URL overlap plus hash-partition consistency. The workflow is read-only.

A live audit on 2026-10-22 found:

- `for-categories`: 648,710 video rows; 648,597 rows with at least one tag; 113 rows still pending tag scraping.
- `gitrepo-updates`: 649,948 video rows; 649,868 rows with at least one tag; 80 rows still pending tag scraping.
- Combined: 1,298,658 rows; 1,298,465 rows with tags; 31,549,215 stored tag entries.
- The two source tables each had the URL primary-key index and tag-scrape pending index. Search/channel/ranking indexes were not previously present.

Use the report from the current run before making capacity or index decisions; row counts and storage sizes change as scraping continues.

### Generic site tag filtering

`scripts/catalog_utils.py` normalizes tags for comparison and removes only generic site boilerplate: `video`, `videos`, `x video`, `x videos`, `xvideos`, and `xvideos.com`, including case and punctuation variants. It is used by both metadata ingestion and HTML tag extraction.

For existing rows, open **Actions → Clean generic video tags**. The default is a dry run that reports affected rows and changes no data. Applying cleanup requires selecting the apply option and entering this exact phrase:

`REMOVE GENERIC TAGS FROM ALL VIDEO ROWS`

The apply path updates matching rows in small commits and leaves other tags intact. Run the dry run first and review its counts before enabling apply.

### Precomputed homepage snapshots

`scripts/build_homepage_snapshots.py` creates compact JSONB snapshots in `public.homepage_snapshots` on **DATABASE_URL_1**. The primary key is `(feed_date, section)`. The builder pulls top candidates from both source databases, deduplicates URLs, excludes generic tags from cards, and constructs three daily sections (`featured`, `popular`, `discovery`) with channel/tag diversity limits. It also stores a ranked canonical tag catalog.

**Actions → Build homepage snapshots** runs after a successful tag-scrape workflow, on a daily schedule, or manually. The default precomputes a three-day window. A website homepage should request these small precomputed payloads from database 1 or serve a generated JSON response through Cloudflare caching; it should not rerank all source videos on each page view.

The builder currently selects candidates from the existing `views` and `rating_percentage` fields. Since the source schema does not currently retain a trustworthy publication timestamp or historical views, this first version ranks popularity/quality rather than claiming true recent-upload or trending order.

### Index migrations prepared for testing

`sql/stage2_source_indexes.sql` defines the candidate-ranking indexes for the two source databases. `sql/homepage_snapshots.sql` defines the snapshot table. The index changes were tested on isolated Neon branches; they have not been applied to production yet.

- `videos_views_rank_idx`: partial B-tree index supporting top-by-views candidate selection.
- `videos_rating_rank_idx`: partial multicolumn B-tree index supporting rating-then-views candidate selection.

Full-text, trigram, GIN tag, and channel indexes should be added only after the catalog serving schema and actual website query shapes are decided. That avoids paying index storage/maintenance costs before confirming which searches the first website release needs.

### Files for this stage

- `scripts/audit_video_catalog.py` — read-only counts, sizes, tags and partition/duplicate audit.
- `scripts/catalog_utils.py` — shared generic-tag filtering.
- `scripts/clean_generic_tags.py` — guarded dry-run/apply cleanup for existing records.
- `scripts/build_homepage_snapshots.py` — precomputes three-day homepage and tag catalog payloads.
- `sql/stage2_source_indexes.sql` and `sql/homepage_snapshots.sql` — versioned schema SQL.
- `.github/workflows/stage2-audit.yml`, `.github/workflows/clean-generic-video-tags.yml`, and `.github/workflows/build-homepage-snapshots.yml` — manual/scheduled actions.
