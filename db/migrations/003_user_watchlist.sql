-- Migration: 003_user_watchlist.sql
-- Description: Create user_watchlist table for storing users' watchlist items

CREATE TABLE IF NOT EXISTS user_watchlist (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    movie_slug TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, movie_slug)
);

-- Index for efficient user-specific watchlist queries
CREATE INDEX IF NOT EXISTS idx_user_watchlist_user_id ON user_watchlist(user_id);

-- Enable Row Level Security
ALTER TABLE user_watchlist ENABLE ROW LEVEL SECURITY;

-- Allow all authenticated users to access their own watchlist
-- (Will be refined in migration 008 with proper RLS policies)
GRANT SELECT, INSERT, DELETE ON user_watchlist TO anon;

-- Comment for documentation
COMMENT ON TABLE user_watchlist IS 'Movies users have saved to watch later';
