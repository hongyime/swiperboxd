-- Migration 004: Diary table
CREATE TABLE IF NOT EXISTS diary (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    movie_slug TEXT NOT NULL REFERENCES movies(slug) ON DELETE CASCADE,
    watched_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(user_id, movie_slug)
);

CREATE INDEX IF NOT EXISTS idx_diary_user_id ON diary(user_id);
CREATE INDEX IF NOT EXISTS idx_diary_watched_at ON diary(watched_at DESC);
