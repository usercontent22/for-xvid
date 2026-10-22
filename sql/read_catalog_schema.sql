-- Optional dedicated read catalog schema.
-- Provision/size a separate catalog database before applying this file.
-- The source databases remain authoritative; this catalog is disposable/rebuildable.
-- Expected source sync field: public.videos.catalog_updated_at (see STAGE2_READ_ARCHITECTURE.md).

CREATE TABLE IF NOT EXISTS public.catalog_videos (
    url TEXT PRIMARY KEY,
    source_db SMALLINT NOT NULL CHECK (source_db IN (1, 2)),
    title TEXT NOT NULL DEFAULT '',
    img TEXT,
    rating_percentage NUMERIC(5,2),
    views BIGINT,
    duration_seconds INTEGER,
    channel_name TEXT,
    channel_key TEXT GENERATED ALWAYS AS (
        regexp_replace(lower(COALESCE(channel_name, '')), '[^[:alnum:]]', '', 'g')
    ) STORED,
    tags TEXT[] NOT NULL DEFAULT '{}'::text[],
    search_vector TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('simple'::regconfig, COALESCE(title, ''))
    ) STORED,
    source_updated_at TIMESTAMPTZ NOT NULL,
    catalog_synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.catalog_sync_state (
    source_db SMALLINT PRIMARY KEY CHECK (source_db IN (1, 2)),
    initial_backfill_done BOOLEAN NOT NULL DEFAULT FALSE,
    last_source_updated_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Tag containment / overlap, e.g. tags @> ARRAY['amateur'] or tags && ARRAY[...].
CREATE INDEX IF NOT EXISTS catalog_videos_tags_gin_idx
    ON public.catalog_videos USING GIN (tags);

-- Word-based title search.
CREATE INDEX IF NOT EXISTS catalog_videos_search_vector_gin_idx
    ON public.catalog_videos USING GIN (search_vector);

-- Channel page sorted by views.
CREATE INDEX IF NOT EXISTS catalog_videos_channel_views_idx
    ON public.catalog_videos (channel_key, views DESC NULLS LAST, url)
    WHERE views IS NOT NULL;

-- Global popular and highly-rated result selection.
CREATE INDEX IF NOT EXISTS catalog_videos_views_rank_idx
    ON public.catalog_videos (views DESC NULLS LAST, url)
    WHERE views IS NOT NULL;

CREATE INDEX IF NOT EXISTS catalog_videos_rating_rank_idx
    ON public.catalog_videos (
        rating_percentage DESC NULLS LAST,
        views DESC NULLS LAST,
        url
    )
    WHERE rating_percentage IS NOT NULL;

-- Optional substring title search. Enable pg_trgm only after confirming
-- extension availability and estimating the extra index size:
-- CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- CREATE INDEX catalog_videos_title_trgm_idx
--     ON public.catalog_videos USING GIN (title gin_trgm_ops);

-- Existing pages can keep using public.homepage_snapshots in the source/read DB
-- or move the same snapshot table here after the catalog project is provisioned.
