-- Migration: 002_rls_policies.sql
-- Description: Enable Row Level Security and create user isolation policies

-- Enable Row Level Security on sensitive tables
ALTER TABLE user_exclusions ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_actions ENABLE ROW LEVEL SECURITY;

-- Policy: Users can only access their own exclusions
CREATE POLICY user_own_exclusions ON user_exclusions
    FOR ALL
    TO anon
    USING (user_id = current_setting('auth.uid', true)::TEXT)
    WITH CHECK (user_id = current_setting('auth.uid', true)::TEXT);

-- Policy: Users can only access their own actions
CREATE POLICY user_own_actions ON user_actions
    FOR ALL
    TO anon
    USING (user_id = current_setting('auth.uid', true)::TEXT)
    WITH CHECK (user_id = current_setting('auth.uid', true)::TEXT);

-- Note: RLS is NOT applied to movies table as it serves as a shared cache
-- All users should have read access to all cached movie metadata
GRANT SELECT ON movies TO anon;

-- Allow authenticated users to read/write their own exclusions
GRANT SELECT, INSERT, DELETE ON user_exclusions TO anon;

-- Allow authenticated users to insert actions (they can only read their own due to RLS)
GRANT INSERT ON user_actions TO anon;

-- Documentation
COMMENT ON POLICY user_own_exclusions ON user_exclusions IS 'Users can only access their own exclusion records';
COMMENT ON POLICY user_own_actions ON user_actions IS 'Users can only access their own action records';
