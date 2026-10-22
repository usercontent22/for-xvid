# Stage 2: read architecture and rollout plan

## What the live audit found

On 2026-10-22, the two source databases contained 1,298,658 videos:

| Source | Videos | Has at least one tag | Tag-scrape pending |
|---|---:|---:|---:|
| for-categories | 648,710 | 648,597 | 113 |
| gitrepo-updates | 649,948 | 649,868 | 80 |
| Combined | 1,298,658 | 1,298,465 | 193 |

The source tables contain about 31.5 million tag entries. Current indexes before Stage 2 were only the URL primary key and the partial tag-scrape-pending index. More than 648,000 rows in each source contain one or more of the supplied generic tags, so a full cleanup should be batched and monitored.

## What is implemented in the repository

- Generic tag filtering is shared by the metadata collector and HTML tag scraper. It removes case/punctuation variants of `video`, `videos`, `x video`, `x videos`, `xvideos`, and `xvideos.com` on future writes/extractions.
- `scripts/clean_generic_tags.py` supports a dry run by default. Rewriting existing rows requires `--apply` plus an exact confirmation phrase.
- `scripts/audit_video_catalog.py` runs read-only row/tag/index/size checks and can compare source URL partitions and cross-database duplicates.
- `scripts/build_homepage_snapshots.py` builds a three-day set of `featured`, `popular`, `discovery`, and `tags` JSONB snapshots from candidates selected in both databases. It limits repeated channels/tags, deduplicates URLs, and removes the generic tags from displayed cards.
- GitHub Actions can run the audit, run guarded cleanup, and precompute snapshots. The homepage builder runs manually, daily, and after a successful tag-scrape run.
- `sql/stage2_source_indexes.sql` defines two ranking indexes for candidate selection.
- `sql/read_catalog_schema.sql` defines a dedicated indexed search catalog design, but it has not been deployed or populated.

## Homepage traffic pattern

The public homepage should not run a top-video query against either source database for every visitor. Instead:
1. A scheduled GitHub Actions job selects small candidate lists from each source.
2. The builder de-duplicates them and writes a compact JSONB payload per date/section to `public.homepage_snapshots` in Database 1.
3. The web app reads the prepared payload and should cache the serialized response at Cloudflare edge, e.g. with a short TTL and stale-while-revalidate behavior if available.
4. Video detail pages and user-submitted searches may query the serving layer when necessary.

The homepage table is additive and small compared with the video catalog. Its production creation is driven by the snapshot workflow's `--init-schema-only` step.

## Indexed read catalog and incremental sync plan

The full catalog should be a separate, rebuildable serving database if production capacity and billing allow it. That avoids cross-database queries for each search request but adds storage. Do not provision or populate it until its storage budget and plan limit are confirmed.

Proposed rollout:

1. Add `catalog_updated_at TIMESTAMPTZ` to both source `videos` tables. Future metadata upserts, tag writes and generic-tag cleanup must set it to `now()`.
2. On the new catalog, create `catalog_videos` and `catalog_sync_state` from `sql/read_catalog_schema.sql`.
3. Backfill URLs from both sources in bounded keyset batches and upsert rows by URL. Record the source database number; compare URL overlap/partition audit before considering the catalog complete.
4. Store a per-source sync watermark. Incremental jobs copy only rows with `catalog_updated_at` newer than the watermark (using a small overlap window so updates committed during a run are not lost); upserts remain idempotent.
5. Benchmark actual queries before adding optional `pg_trgm` substring-search indexing. The required GIN/B-tree indexes will need measured size and performance tests.

## Production Stage 2 changes completed

The generic-tag cleanup was applied to both configured source databases in bounded 5,000-row batches. A normalized audit query now returns zero video rows containing the generic keys `video`, `videos`, `xvideo`, `xvideos`, or `xvideoscom`. The cleanup changed 649,868 rows from Database URL 1 and 648,597 rows from Database URL 2; no other tags were removed.

During the first cleanup attempt, Neon rejected further writes after its 1 GiB logical-size limit was exceeded. The views/rating indexes were temporarily dropped, the cleanup resumed idempotently and completed, and the two indexes were rebuilt after the cleanup. The temporary push trigger used for the approved run has been removed; the cleanup workflow is manual-only and defaults to dry-run.

The views and rating ranking indexes are now present in both source databases:
- `videos_views_rank_idx`: about 71 MB per database; partial B-tree for top-by-views selection.
- `videos_rating_rank_idx`: about 76 MB per database; partial multicolumn B-tree for rating-then-views selection.

In temporary branches created after cleanup, EXPLAIN plans for top-100 ranking queries used index-only scans for both ranking indexes on both source projects. Earlier ANALYZE runs (before the historical tag cleanup) measured about 6.5 ms for views ranking and 2.3 ms for rating-then-views ranking; these observations are not production latency guarantees.

Current database sizes are approximately 808 MB for `for-categories` and 907 MB for `gitrepo-updates`, against a 1 GiB logical-size limit. Keep new large indexes off the source databases. The GIN tag, title-search, channel and full catalog indexes belong in the separately budgeted read catalog.

The homepage snapshot table was created on the first configured source database, and the initial snapshot builder workflow completed successfully. It stored 13 rows for October 22–24, 2026: daily `featured`, `popular`, `discovery`, and `tags` sections plus the October 22 canonical tag catalog. Each featured/popular/discovery section has 48 items; each day’s tags section has 60 items; the canonical catalog has 60 items.

## Still not provisioned

The dedicated read catalog has not been created or populated. Its schema and incremental-sync approach are documented in `sql/read_catalog_schema.sql` and this file. Before provisioning it, decide the storage budget and source-to-catalog sync strategy. The source databases should remain authoritative; the website should use the catalog for full search/filter operations and prepared homepage snapshots for homepage traffic.
