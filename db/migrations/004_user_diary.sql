-- Migration: 004_user_diary.sql
-- Description: Create user_diary table for storing users' diary (watched films)

CREATE TABLE IF NOT EXISTS user_diary (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    movie_slug TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, movie_slug)
);

-- Index for efficient user-specific diary queries
CREATE INDEX IF NOT EXISTS idx_user_diary_user_id ON user_diary(user_id);

-- Enable Row Level Security
ALTER TABLE user_diary ENABLE ROW LEVEL SECURITY;

-- Allow all authenticated users to access their own diary
-- (Will be refined in migration 008 with proper RLS policies)
GRANT SELECT, INSERT, DELETE ON user_diary TO anon;

-- Comment for documentation
COMMENT ON TABLE user_diary IS 'Movies users have already watched (diary entries)';
