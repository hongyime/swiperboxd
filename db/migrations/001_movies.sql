-- Migration 001: Movies table
CREATE TABLE IF NOT EXISTS movies (
    id BIGSERIAL PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    rating FLOAT DEFAULT NULL,
    popularity INTEGER DEFAULT 0,
    poster_url TEXT,
    synopsis TEXT,
    genres JSONB DEFAULT '[]',
    "cast" JSONB DEFAULT '[]',
    year INTEGER,
    director TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_movies_slug ON movies(slug);
CREATE INDEX IF NOT EXISTS idx_movies_rating ON movies(rating DESC);
CREATE INDEX IF NOT EXISTS idx_movies_popularity ON movies(popularity DESC);
