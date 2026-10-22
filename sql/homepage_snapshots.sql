-- Small, additive table for precomputed homepage payloads.
-- Intended for DATABASE_URL_1; the website reads these snapshots rather than
-- rebuilding feeds from both source databases on every homepage visit.
CREATE TABLE IF NOT EXISTS public.homepage_snapshots (
    feed_date DATE NOT NULL,
    section TEXT NOT NULL,
    payload JSONB NOT NULL,
    source_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (feed_date, section)
);
