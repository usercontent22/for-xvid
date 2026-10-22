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

## Index tests already performed

The proposed views and rating ranking indexes were applied only to temporary Neon branches. Representative top-100 ranking queries used index-only scans on those branches:
- Views ranking: approximately 6.5 ms execution.
- Rating then views ranking: approximately 2.3 ms execution.

These figures are a single test observation, not a production latency guarantee. Temporary test databases showed about 71 MB for the views index and 76 MB for the rating index. Both source databases remained below the reported 1 GiB logical-size limit in the test branches after these indexes, but this does not measure future catalog storage.

## Production changes requiring explicit confirmation

- The two source ranking indexes are tested but have not been applied to the production branches.
- The homepage snapshots table and actual three-day data have not yet been created in production. They will be created when the snapshot workflow runs.
- The historical generic-tag cleanup has not been run; the action defaults to dry-run.
- The dedicated read catalog has not been provisioned or populated.
