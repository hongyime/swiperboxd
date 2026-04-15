-- Migration 006: Genre preferences table
CREATE TABLE IF NOT EXISTS genre_preferences (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    genre TEXT NOT NULL,
    score FLOAT DEFAULT 0,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(user_id, genre)
);

CREATE INDEX IF NOT EXISTS idx_genre_preferences_user_id ON genre_preferences(user_id);
