-- Migration: 001_initial_schema.sql
-- Description: Create base tables for CineSwipe application

-- user_exclusions table: Track movies users want to exclude from recommendations
CREATE TABLE IF NOT EXISTS user_exclusions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    movie_slug TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, movie_slug)
);

-- Index for efficient user-specific exclusion queries
CREATE INDEX IF NOT EXISTS idx_user_exclusions_user_id ON user_exclusions(user_id);

-- movies table: Cache-aside storage for movie metadata
CREATE TABLE IF NOT EXISTS movies (
    slug TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    poster_url TEXT NOT NULL,
    rating FLOAT NOT NULL,
    popularity INTEGER NOT NULL,
    genres JSONB NOT NULL,
    synopsis TEXT,
    cast JSONB,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for common discovery filters
CREATE INDEX IF NOT EXISTS idx_movies_rating ON movies(rating);
CREATE INDEX IF NOT EXISTS idx_movies_popularity ON movies(popularity);
CREATE INDEX IF NOT EXISTS idx_movies_updated_at ON movies(updated_at);

-- user_actions table: Audit log for user interactions
CREATE TABLE IF NOT EXISTS user_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    movie_slug TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('watchlist', 'dismiss', 'log')),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for action history queries (most recent first)
CREATE INDEX IF NOT EXISTS idx_user_actions_user_id ON user_actions(user_id, created_at DESC);

-- Comment for documentation
COMMENT ON TABLE user_exclusions IS 'Movies excluded from recommendation (dismissed swipes)';
COMMENT ON TABLE movies IS 'Cached movie metadata from external sources';
COMMENT ON TABLE user_actions IS 'Audit log of user swipe actions';
