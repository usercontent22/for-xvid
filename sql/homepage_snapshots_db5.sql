-- Compact feed cache only. Keep this in DATABASE_URL_5, not on the video shards.
-- Twelve feed rows per three-day window plus a single canonical tag catalog stay tiny.
CREATE TABLE IF NOT EXISTS public.homepage_snapshots (
    feed_date DATE NOT NULL,
    section TEXT NOT NULL,
    payload JSONB NOT NULL,
    source_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (feed_date, section)
);

-- Do not create a JSONB GIN index: all production reads are point/date/section lookups.
