-- Ranking indexes used by the scheduled homepage snapshot builder.
-- These are for the two ingestion/source databases. They keep the daily
-- candidate-pool selection from sorting the full table for views/rating.
-- The separate full search catalog needs its own index plan.
CREATE INDEX IF NOT EXISTS videos_views_rank_idx
    ON public.videos (views DESC NULLS LAST, url)
    WHERE views IS NOT NULL;

CREATE INDEX IF NOT EXISTS videos_rating_rank_idx
    ON public.videos (rating_percentage DESC NULLS LAST, views DESC NULLS LAST, url)
    WHERE rating_percentage IS NOT NULL;
