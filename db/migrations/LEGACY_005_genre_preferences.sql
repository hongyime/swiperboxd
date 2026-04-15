-- Migration: 005_genre_preferences.sql
-- Description: Create genre_preferences table for storing users' genre weight preferences

CREATE TABLE IF NOT EXISTS genre_preferences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    genre TEXT NOT NULL,
    weight INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, genre)
);

-- Index for efficient user-specific genre preference queries
CREATE INDEX IF NOT EXISTS idx_genre_preferences_user_id ON genre_preferences(user_id);

-- Enable Row Level Security
ALTER TABLE genre_preferences ENABLE ROW LEVEL SECURITY;

-- Allow all authenticated users to access their own genre preferences
-- (Will be refined in migration 008 with proper RLS policies)
GRANT SELECT, INSERT, UPDATE ON genre_preferences TO anon;

-- Comment for documentation
COMMENT ON TABLE genre_preferences IS 'User genre preferences for weighted movie recommendations';
COMMENT ON COLUMN genre_preferences.weight IS 'Weight score indicating how much user likes this genre (higher = more preferred)';
