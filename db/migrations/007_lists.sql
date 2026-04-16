-- Migration 007: List catalog and membership tables
-- Provides persistence for the list discovery system.
-- list_summaries: one row per Letterboxd list (catalog entry)
-- list_memberships: ordered film membership within a list

CREATE TABLE IF NOT EXISTS list_summaries (
    list_id TEXT PRIMARY KEY,
    slug TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    owner_name TEXT DEFAULT '',
    owner_slug TEXT DEFAULT '',
    description TEXT DEFAULT '',
    film_count INTEGER DEFAULT 0,
    like_count INTEGER DEFAULT 0,
    comment_count INTEGER DEFAULT 0,
    is_official BOOLEAN DEFAULT FALSE,
    tags JSONB DEFAULT '[]',
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS list_memberships (
    id BIGSERIAL PRIMARY KEY,
    list_id TEXT NOT NULL REFERENCES list_summaries(list_id) ON DELETE CASCADE,
    movie_slug TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    UNIQUE(list_id, movie_slug)
);

CREATE INDEX IF NOT EXISTS idx_list_memberships_list_id_pos
    ON list_memberships(list_id, position);
