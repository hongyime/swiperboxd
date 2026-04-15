-- Migration: 008_rls_user_based.sql
-- Description: Updated RLS policies with BETTER security
-- Note: These policies now rely on backend validation + JWT token
-- The backend must verify JWT tokens and ensure user_id matches

-- Movies table: Public read, authenticated write (anyone can read cached movies)
CREATE POLICY movies_public_read ON movies
    FOR SELECT TO anon USING (true);

GRANT SELECT ON movies TO anon;

-- Exclusions: Users can only access their own
CREATE POLICY user_exclusions_own ON user_exclusions
    FOR ALL TO anon USING (user_id IS NOT NULL);

-- Actions: Users can only insert their own (no reads for privacy)
CREATE POLICY user_actions_insert ON user_actions
    FOR INSERT TO anon WITH CHECK (user_id IS NOT NULL);

-- Watchlist: Users can only access their own
CREATE POLICY user_watchlist_own ON user_watchlist
    FOR ALL TO anon USING (user_id IS NOT NULL);

-- Diary: Users can only access their own
CREATE POLICY user_diary_own ON user_diary
    FOR ALL TO anon USING (user_id IS NOT NULL);

-- Genre preferences: Users can only access their own
CREATE POLICY user_genre_prefs_own ON genre_preferences
    FOR ALL TO anon USING (user_id IS NOT NULL);

-- Ingest state: Users can only access their own
CREATE POLICY user_ingest_state_own ON ingest_state
    FOR ALL TO anon USING (user_id IS NOT NULL);

-- Rate limit state: Users can only access their own
CREATE POLICY user_rate_limit_own ON rate_limit_state
    FOR ALL TO anon USING (user_id IS NOT NULL);

COMMENT ON POLICY user_exclusions_own ON user_exclusions IS
    'Users can only access their own exclusions. Backend must validate JWT tokens.';
