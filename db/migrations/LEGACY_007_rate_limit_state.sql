-- Migration: 007_rate_limit_state.sql
-- Description: Create rate_limit_state table for persisting rate limiting state

CREATE TABLE IF NOT EXISTS rate_limit_state (
    user_id TEXT PRIMARY KEY,
    last_action_at FLOAT NOT NULL,
    last_scrape_at FLOAT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for finding stale rate limit entries
CREATE INDEX IF NOT EXISTS idx_rate_limit_state_updated_at ON rate_limit_state(updated_at);

-- Allow access to all users (rate limit state is not sensitive)
GRANT SELECT, INSERT, UPDATE ON rate_limit_state TO anon;

-- Comment for documentation
COMMENT ON TABLE rate_limit_state IS 'Rate limiting state persistence for each user';
COMMENT ON COLUMN rate_limit_state.last_action_at IS 'Timestamp of last swipe action (ms since epoch)';
COMMENT ON COLUMN rate_limit_state.last_scrape_at IS 'Timestamp of last scrape request (seconds since epoch)';
