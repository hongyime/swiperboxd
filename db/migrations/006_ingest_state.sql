-- Migration: 006_ingest_state.sql
-- Description: Create ingest_state table for persisting ingest progress and running state

CREATE TABLE IF NOT EXISTS ingest_state (
    user_id TEXT PRIMARY KEY,
    progress INTEGER NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
    running BOOLEAN NOT NULL DEFAULT FALSE,
    source TEXT,
    depth_pages INTEGER,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for finding stale ingest jobs
CREATE INDEX IF NOT EXISTS idx_ingest_state_updated_at ON ingest_state(updated_at);

-- Allow access to all users (ingest state is not sensitive)
GRANT SELECT, INSERT, UPDATE ON ingest_state TO anon;

-- Comment for documentation
COMMENT ON TABLE ingest_state IS 'Ingest progress and state for each user';
COMMENT ON COLUMN ingest_state.progress IS 'Ingest completion percentage (0-100)';
COMMENT ON COLUMN ingest_state.running IS 'Whether ingest job is currently running';
