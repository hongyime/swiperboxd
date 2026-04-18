-- Migration 008: Track scrape completeness per list
ALTER TABLE list_summaries ADD COLUMN IF NOT EXISTS scraped_film_count INTEGER DEFAULT 0;
