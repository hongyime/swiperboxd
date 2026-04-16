-- Seed initial list data for testing
-- Insert 3-5 popular Letterboxd lists to bootstrap the app

INSERT INTO list_summaries (list_id, slug, url, title, owner_name, owner_slug, description, film_count, like_count, comment_count, is_official, tags, updated_at) VALUES
('letterboxd-official-1001', 'top-250', 'https://letterboxd.com/letterboxd/list/top-250/', 'Letterboxd’s Top 250', 'Letterboxd Official', 'letterboxd', 'The Top 250 rated narrative feature films on Letterboxd.', 250, 156000, 4200, true, '["official", "top-rated"]', NOW()),
('letterboxd-official-1002', 'best-picture-winners', 'https://letterboxd.com/letterboxd/list/best-picture-winners/', 'Academy Award Winners: Best Picture', 'Letterboxd Official', 'letterboxd', 'Every film that has won the Academy Award for Best Picture.', 95, 120000, 3800, true, '["official", "awards"]', NOW()),
('letterboxd-trending-001', 'trending-films-2024', 'https://letterboxd.com/list/trending-films-2024/', 'Trending Films of 2024', 'Community Curated', 'community', 'The most popular films trending on Letterboxd in 2024.', 100, 45000, 1200, false, '["community", "trending", "2024"]', NOW()),
('letterboxd-list-gems', 'hidden-gems', 'https://letterboxd.com/letterboxd/list/hidden-gems/', 'Hidden Gems You Need to Watch', 'Letterboxd Staff', 'letterboxd', 'Lesser-known films that deserve more attention.', 50, 32000, 1500, false, '["staff-picks", "hidden-gems"]', NOW()),
('letterboxd-genre-horror', 'best-horror-films', 'https://letterboxd.com/list/best-horror-films/', 'Essential Horror Films', 'Genre Experts', 'community', 'The most acclaimed horror films of all time.', 75, 28000, 980, false, '["genre", "horror", "curated"]', NOW());

-- Verify insertion
SELECT list_id, title, owner_name, film_count, like_count FROM list_summaries ORDER BY like_count DESC;
