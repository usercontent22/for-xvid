# XXX World

A modular Flask video-discovery website for Vercel. The metadata collector workflows remain separate from the web-serving layer.

## Structure

- app.py: Flask/Vercel entrypoint and response headers.
- web/db.py: bounded PostgreSQL reads and exact URL-primary-key lookup.
- web/repositories/snapshots.py: tiny indexed homepage snapshot reads.
- web/catalog.py: optional indexed catalog queries with cursor/keyset pagination.
- web/media.py: thumbnail, hover-preview, gallery, duration and view-format helpers.
- web/routes/: homepage, browsing, search, channel, video details and image gallery blueprints.
- templates/: reusable Jinja templates, including one shared video-card partial.
- public/: CSS and JavaScript served as static files.

## Vercel environment

DATABASE_URL_1 and DATABASE_URL_2 are already configured in the project. They should remain server-only.

Configure these additional variables in Vercel:

- THUMBNAIL_BASE_URL: the base host that serves paths stored in videos.img. The example host is configured as a fallback, but use your actual current image host.
- STATIC_ASSETS_BASE_URL: public URL base for the files in the public Neon Object Storage path for-xvide/static. It should be the directory URL immediately above the logo/icon filenames.
- PREVIEW_BASE_URL: optional. Use only if preview.mp4 files are hosted on a different domain than thumbnails.
- SITE_URL: optional canonical site origin for sitemap URLs.
- CATALOG_DATABASE_URL: optional, only after you have provisioned and populated the separate indexed read catalog.

Do not prefix database variables with NEXT_PUBLIC or use them in browser code. The website never returns database URLs from health or application endpoints.

Static branding files expected below STATIC_ASSETS_BASE_URL:
xxxworld-logo.svg, xxxworld-logo.webp, xxxworld-icon.svg, xxxworld-icon-32.webp, xxxworld-icon-64.webp, xxxworld-icon-128.webp, xxxworld-icon-256.webp, xxxworld-icon-512.webp and favicon.ico.

## Database and load strategy

1. The homepage reads the prepared public.homepage_snapshots table in DATABASE_URL_1; it never recomputes ranking queries for each visitor.
2. Video details decode a stable URL-safe route token and look up videos.url, the existing primary key. No new numeric video ID migration is required.
3. No route runs COUNT(*) against the video catalog. Once a dedicated read catalog is configured, listing/search routes use keyset pagination and fetch one extra record to detect a next page instead of calculating total pages.
4. Full search and complete channel/category listings use CATALOG_DATABASE_URL only. The two source databases lack title-search and tag/channel indexes, and their storage budgets are tight. Until the serving catalog is available, browse and search deliberately use a bounded pool of precomputed highlights rather than issuing expensive full-catalog scans.
5. Source thumbnail paths such as /asset-id/0/xv_2_t.jpg are combined with THUMBNAIL_BASE_URL. Hover preview paths are derived as /asset-id/0/preview.mp4. Preview videos use preload=none and are only requested after a short hover delay.
6. Channel URLs preserve the exact channel name in a URL-safe token, so spaces and punctuation do not break the route. A video thumbnail provides a stable avatar image on the detail page. A future catalog can choose and store a consistent channel avatar per channel.
7. The image gallery generates numbered frame URLs from 1 to 30 to support image-oriented discovery. The remote source still has to provide each numbered image.
8. Response cache headers allow Vercel's shared cache to reuse anonymous GET responses. The homepage has a short shared cache lifetime; assets are static CDN files.

The proposed catalog schema is in sql/read_catalog_schema.sql. It is intentionally not created by this website: first budget for it, create it separately, backfill in batches, establish incremental sync, and verify indexes and sizes. Keep the source tables as the metadata system of record.

## Routes

- / — snapshot-backed homepage.
- /browse?section=featured|popular|discovery — prebuilt video collections.
- /search?q=... — indexed search when the serving catalog is configured; otherwise searches only the prepared highlight pool.
- /category/<slug>--<token> — tag/category page.
- /channel/<slug>--<token> — channel page; supports channel names with spaces.
- /video/<slug>--<token> — detail page, exact URL-primary-key lookup.
- /images/<slug>--<token> — numbered thumbnail gallery.
- /healthz — non-secret deployment health indicators.
- /sitemap.xml — URLs from the current snapshot pool.

Full video playback URL resolution is intentionally not implemented yet because its rules are pending. The detail page shows the preview separately from the future full-playback source.

The browser age prompt is only a basic notice and a browser preference; it is not identity or legal-age verification. Review age-access requirements for each place you operate.

## Local run

1. Create a Python virtual environment.
2. Install dependencies from requirements.txt.
3. Copy .env.example to a local .env file and fill in values; never commit secrets.
4. Start with python app.py and visit http://127.0.0.1:5000.

No production database schema is altered and no indexes are added by this app. Existing GitHub Actions continue testing the collectors and now compile the web modules as well.
