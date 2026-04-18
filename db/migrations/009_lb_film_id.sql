-- Migration 009: Add Letterboxd film LID to movies table
-- The LID (e.g. "NTi2") is returned in the x-letterboxd-identifier response
-- header and is required for the official /api/v0/me/watchlist/{id} endpoint.
ALTER TABLE movies ADD COLUMN IF NOT EXISTS lb_film_id TEXT;
CREATE INDEX IF NOT EXISTS idx_movies_lb_film_id ON movies(lb_film_id);
