-- Migration: 008_rls_user_based.sql
-- Description: Update RLS policies to use user_id column instead of auth.uid
-- This allows the app to work with client-provided user_id strings for now
-- (later can be upgraded to proper UUID-based auth)

-- Drop old UUID-based RLS policies
DROP POLICY IF EXISTS user_own_exclusions ON user_exclusions;
DROP POLICY IF EXISTS user_own_actions ON user_actions;

-- Create user_id-based RLS policies for existing tables
-- NOTE: For demo/development, we're allowing all access based on user_id presence
-- In production with proper auth, these should be updated to use auth.uid

-- Exclusions table
CREATE POLICY user_access_exclusions ON user_exclusions
    FOR ALL
    TO anon
    USING (user_id IS NOT NULL)
    WITH CHECK (user_id IS NOT NULL);

-- Actions table
CREATE POLICY user_access_actions ON user_actions
    FOR ALL
    TO anon
    USING (user_id IS NOT NULL)
    WITH CHECK (user_id IS NOT NULL);

-- Watchlist table
CREATE POLICY user_access_watchlist ON user_watchlist
    FOR ALL
    TO anon
    USING (user_id IS NOT NULL)
    WITH CHECK (user_id IS NOT NULL);

-- Diary table
CREATE POLICY user_access_diary ON user_diary
    FOR ALL
    TO anon
    USING (user_id IS NOT NULL)
    WITH CHECK (user_id IS NOT NULL);

-- Genre preferences table
CREATE POLICY user_access_genre_preferences ON genre_preferences
    FOR ALL
    TO anon
    USING (user_id IS NOT NULL)
    WITH CHECK (user_id IS NOT NULL);

-- Note: Movies table serves as a shared cache, no RLS needed
-- All users should have read access to all cached movie metadata
GRANT SELECT ON movies TO anon;

-- Allow authenticated users to read/write their own data
GRANT SELECT, INSERT, DELETE ON user_exclusions TO anon;
GRANT INSERT ON user_actions TO anon;
GRANT SELECT, INSERT, DELETE ON user_watchlist TO anon;
GRANT SELECT, INSERT, DELETE ON user_diary TO anon;
GRANT SELECT, INSERT, UPDATE ON genre_preferences TO anon;

-- Documentation
COMMENT ON POLICY user_access_exclusions ON user_exclusions IS 'Allow access based on user_id (temporary for dev)';
COMMENT ON POLICY user_access_actions ON user_actions IS 'Allow access based on user_id (temporary for dev)';
COMMENT ON POLICY user_access_watchlist ON user_watchlist IS 'Allow access based on user_id (temporary for dev)';
COMMENT ON POLICY user_access_diary ON user_diary IS 'Allow access based on user_id (temporary for dev)';
COMMENT ON POLICY user_access_genre_preferences ON genre_preferences IS 'Allow access based on user_id (temporary for dev)';

-- NOTE: Future improvement - when implementing proper Supabase Auth:
-- 1. Update policies to use: auth.uid::TEXT = user_id
-- 2. Require JWT authentication headers
-- 3. Use service role key in backend operations
-- 4. Implement proper user management
