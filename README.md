# XXX World

A modular Flask video-discovery website for Vercel. The metadata collector workflows remain separate from the web-serving layer.

## Structure

- app.py: Flask/Vercel entrypoint and response headers.
- web/db.py: bounded DB3/DB4 PostgreSQL reads and exact URL-primary-key lookup.
- web/repositories/snapshots.py: Redis-first homepage feed reads with DB5 as a fallback.
- web/catalog.py: indexed parallel-shard queries with cursor/keyset pagination.
- web/media.py: thumbnail, hover-preview, gallery, duration and view-format helpers.
- web/routes/: homepage, browsing, search, channel, video details and image gallery blueprints.
- templates/: reusable Jinja templates, including one shared video-card partial.
- public/: CSS and JavaScript served as static files.

## Vercel environment

The live website uses three server-only PostgreSQL variables plus a server-only Upstash Redis URL:

- `DATABASE_URL_3`: first video-data shard.
- `DATABASE_URL_4`: second video-data shard.
 - `DATABASE_URL_5`: tiny homepage snapshot/tag-feed fallback store.
- `REDIS_URL`: Upstash Redis TLS connection URL (`rediss://...`), used only by server-side Python and GitHub Actions.

The app no longer reads DB1 or DB2. Detail routes choose DB3 or DB4 from the same stable SHA-256 URL partition used by ingestion; list/search routes query the two shards in parallel and merge only the top page. DB3 and DB4 keep the actual video rows and compact query indexes. DB5 stores only the small prepared JSON feed snapshots—not a duplicate catalog.

Also configure `THUMBNAIL_BASE_URL`, `STATIC_ASSETS_BASE_URL`, optional `PREVIEW_BASE_URL`, and optional `SITE_URL`. Do not prefix database variables with `NEXT_PUBLIC` or expose them in browser code. The health endpoint reports only whether server-side configuration is present, never connection strings.

Static branding files expected below STATIC_ASSETS_BASE_URL:
xxxworld-logo.svg, xxxworld-logo.webp, xxxworld-icon.svg, xxxworld-icon-32.webp, xxxworld-icon-64.webp, xxxworld-icon-128.webp, xxxworld-icon-256.webp, xxxworld-icon-512.webp and favicon.ico.

## Database and load strategy

1. The homepage reads date-versioned feed keys from Redis first. If Redis misses or is unavailable, the app falls back to the tiny prepared `public.homepage_snapshots` table in DB5; visitors never trigger feed ranking.
2. Video detail routes select DB3 or DB4 using the URL hash shard, with the other shard as a fallback. Lookups use the existing `videos.url` primary key.
3. Browse/search/channel/category pages query DB3 and DB4 in parallel, use keyset pagination, and merge only a bounded page. Redis caches successful catalog pages for 5 minutes and search pages for 90 seconds; partial results during a shard outage are not cached. There is no `OFFSET`, full-table `COUNT(*)`, or duplicated 1.3M-row catalog.
4. Repeated exact video lookups are cached in Redis for 30 minutes. DB3/DB4 still use B-tree indexes for URL lookup, ranking, and channel pages, plus one GIN expression index for title/channel full-text and a compact GIN array index for exact tags. DB5 has no GIN/JSONB index; snapshot reads are bounded by the primary key/date section.
5. Source thumbnail paths such as /asset-id/0/xv_2_t.jpg are combined with THUMBNAIL_BASE_URL. Hover preview paths are derived as /asset-id/0/preview.mp4. Preview videos use preload=none and are only requested after a short hover delay.
6. Upstash Redis is a disposable cache, not the source of truth. Prepared feed keys have a 9-day expiry but are refreshed after 3 days; the canonical tag catalogue has a 14-day expiry and is refreshed after 7 days. The homepage cache workflow checks freshness daily and exits before Neon queries if both are still fresh. Add `REDIS_URL` to both Vercel server-side environment variables and GitHub Actions repository secrets; never commit its value.
6. Channel URLs preserve the exact channel name in a URL-safe token, so spaces and punctuation do not break the route. A video thumbnail provides a stable avatar image on the detail page. A future catalog can choose and store a consistent channel avatar per channel.
7. The image gallery generates numbered frame URLs from 1 to 30 to support image-oriented discovery. The remote source still has to provide each numbered image.
8. Response cache headers allow Vercel's shared cache to reuse anonymous GET responses. The homepage has a short shared cache lifetime; assets are static CDN files.

The serving index SQL is in `sql/serving_indexes_db3_db4.sql`; the compact DB5 snapshot schema is in `sql/homepage_snapshots_db5.sql`. These leave the video rows in DB3/DB4 and avoid a second full catalog copy.


## Initial database migration

The manual **Migrate videos from DB1 to DB3** GitHub Actions workflow copies
`public.videos` from `DATABASE_URL_1` into the existing table in
`DATABASE_URL_3` in resumable keyset batches. It uses PostgreSQL `COPY` into
a temporary staging table, saves a transaction-safe checkpoint, skips existing
URLs, and copies at most 10 tags per row. The workflow defaults to one 10,000-row
smoke-test batch; review DB3 and then rerun with `max_batches=0` to finish.
Add `DATABASE_URL_3` as a repository Actions secret before running it. Details
are in [VIDEO_METADATA_WORKFLOW.md](VIDEO_METADATA_WORKFLOW.md#stage-3-initial-batch-migration-into-database_url_3).


### DB2 → DB4 migration

The manual **Migrate videos from DB2 to DB4** action copies `public.videos`
from `DATABASE_URL_2` into `DATABASE_URL_4` using the same resumable 10,000-row
default batches, PostgreSQL `COPY` staging, and 10-tags-per-row limit as DB1 → DB3.
Add `DATABASE_URL_4` as a repository Actions secret before running. It has a
separate checkpoint and retries transient failures up to three attempts. See
[VIDEO_METADATA_WORKFLOW.md](VIDEO_METADATA_WORKFLOW.md#stage-4-initial-batch-migration-into-database_url_4).

## Routes

- / — snapshot-backed homepage.
- /browse?section=featured|popular|discovery — prebuilt video collections.
- /search?q=... — indexed title/channel full-text and exact-tag search across DB3/DB4, with bounded keyset pagination.
- /category/<slug>--<token> — tag/category page.
- /channel/<slug>--<token> — channel page; supports channel names with spaces.
- /video/<slug>--<token> — detail page, exact URL-primary-key lookup.
- /images/<slug>--<token> — numbered thumbnail gallery.
- /healthz — non-secret deployment health indicators.
- /sitemap.xml — URLs from the current snapshot pool.

Full HLS playback is resolved by the Neon Function source in `neon/functions/hlsresolve/index.js`. Configure the public function invocation URL as server-side `HLS_RESOLVER_URL`; the detail page asks the resolver only after a visitor selects **Play full video**. The resolver accepts only catalog-style `/<id>/<slug>` paths and transforms `/aaabd271c/my_video` to `https://www.xvideos.com/video.aaabd271c/my_video`, then scans the returned page for an openly exposed HTTPS `.m3u8` playlist. It does not bypass logins, captchas, DRM, or other access controls. Playback depends on the source playlist being available and allowing cross-origin HLS requests. The detail page shows four same-channel thumbnails in the right column and renders related cards underneath the player.

The browser age prompt is only a basic notice and a browser preference; it is not identity or legal-age verification. Review age-access requirements for each place you operate.

## Local run

1. Create a Python virtual environment.
2. Install dependencies from requirements.txt.
3. Copy .env.example to a local .env file and fill in values; never commit secrets.
4. Start with python app.py and visit http://127.0.0.1:5000.

No production database schema is altered and no indexes are added by this app. Existing GitHub Actions continue testing the collectors and now compile the web modules as well.
