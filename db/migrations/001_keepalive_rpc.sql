-- ============================================================
-- Migration: Add Keepalive RPC Function
-- Purpose: RPC function for GitHub Actions to ping and prevent pause
-- Date: 2025-05-05
-- ============================================================

CREATE OR REPLACE FUNCTION keep_alive_ping()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    result JSONB;
BEGIN
    -- Create a simple ping record in the logs table
    INSERT INTO public.keepalive_logs (source)
    VALUES ('github-actions')
    ON CONFLICT DO NOTHING;
    
    -- Return success
    result := jsonb_build_object(
        'status', 'success',
        'timestamp', now(),
        'message', 'Database is alive'
    );
    
    RETURN result;
END;
$$;

-- Grant execute permission to anon and authenticated
GRANT EXECUTE ON FUNCTION keep_alive_ping() TO anon;
GRANT EXECUTE ON FUNCTION keep_alive_ping() TO authenticated;

-- Create keepalive_logs table if it doesn't exist
CREATE TABLE IF NOT EXISTS public.keepalive_logs (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    pinged_at   TIMESTAMPTZ DEFAULT NOW(),
    source      TEXT        DEFAULT 'github-actions'
);

CREATE INDEX IF NOT EXISTS idx_keepalive_pinged_at ON public.keepalive_logs(pinged_at);

-- Enable RLS on keepalive_logs
ALTER TABLE public.keepalive_logs ENABLE ROW LEVEL SECURITY;

-- Allow all to select and insert
CREATE POLICY IF NOT EXISTS "Allow keepalive for all"
    ON public.keepalive_logs
    FOR ALL
    TO anon, authenticated
    USING (true)
    WITH CHECK (true);

-- Grant permissions
GRANT SELECT, INSERT ON public.keepalive_logs TO anon;
GRANT SELECT, INSERT ON public.keepalive_logs TO authenticated;

SELECT jsonb_build_object('status', 'setup_complete', 'message', 'Keepalive RPC function created') as result;
