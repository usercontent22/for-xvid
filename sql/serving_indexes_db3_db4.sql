-- Lean serving indexes for the two existing data shards (DB3 and DB4).
-- Run this file once against each database after the serving migration is approved.
-- Existing primary/ranking indexes are reused when already present; no duplicate full-text
-- catalog table is created. Every index below matches a query in web/catalog.py.

-- Ranking indexes match ORDER BY descending on all key columns via a backward B-tree scan.
CREATE INDEX IF NOT EXISTS videos_views_rank_idx
    ON public.videos (views, url);

CREATE INDEX IF NOT EXISTS videos_rating_rank_idx
    ON public.videos (rating_percentage, views, url);

-- Channel pages: normalized key + popularity, matching normalize_channel_key().
CREATE INDEX IF NOT EXISTS videos_channel_views_idx
    ON public.videos (
        (regexp_replace(lower(COALESCE(channel_name, '')), '[^a-z0-9]', '', 'g')),
        views,
        url
    );

-- Broad search across title, channel and tags. The expression must match web/catalog.py.
CREATE INDEX IF NOT EXISTS videos_search_fts_idx
    ON public.videos USING GIN (
        to_tsvector(
            'simple'::regconfig,
            COALESCE(title, '') || ' ' ||
            COALESCE(channel_name, '')
        )
    );

-- Exact tag/category containment: smaller than maintaining a second text-search GIN.
CREATE INDEX IF NOT EXISTS videos_tags_gin_idx
    ON public.videos USING GIN (tags);

-- Tag-scraper bookkeeping: only rows with no tags and no scrape timestamp are indexed.
ALTER TABLE public.videos
    ADD COLUMN IF NOT EXISTS tags_scraped_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS videos_tags_pending_idx
    ON public.videos (url)
    WHERE tags_scraped_at IS NULL AND cardinality(tags) = 0;

ANALYZE public.videos;
