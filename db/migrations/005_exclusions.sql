-- Migration 005: Exclusions table
CREATE TABLE IF NOT EXISTS exclusions (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    movie_slug TEXT NOT NULL REFERENCES movies(slug) ON DELETE CASCADE,
    added_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(user_id, movie_slug)
);

CREATE INDEX IF NOT EXISTS idx_exclusions_user_id ON exclusions(user_id);
