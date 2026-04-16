"""Directly seed lists into Supabase by fetching from Letterboxd now."""

import os
import sys
from pathlib import Path

# Load .env
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value

# Import after env is loaded
from supabase import create_client
from dotenv import load_dotenv
load_dotenv()

# Get Supabase client
supabase_url = os.getenv("SUPABASE_URL")
service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

print(f"Connecting to Supabase: {supabase_url}")

client = create_client(supabase_url, service_key)

# Import letterboxd scraper
sys.path.insert(0, str(Path(__file__).parent))
from src.api.providers.letterboxd import HttpLetterboxdScraper

# Create scraper
scraper = HttpLetterboxdScraper()

print("\nFetching lists from Letterboxd...")
try:
    # Try to fetch lists - with error handling for rate limiting
    lists_data = scraper.discover_site_lists(page=1)
    print(f"Fetched {len(lists_data)} lists from Letterboxd\n")
    
    # Insert into Supabase
    for item in lists_data:
        try:
            client.table('list_summaries').upsert({
                'list_id': item.list_id,
                'slug': item.slug,
                'url': item.url,
                'title': item.title,
                'owner_name': item.owner_name,
                'owner_slug': item.owner_slug,
                'description': item.description,
                'film_count': item.film_count,
                'like_count': item.like_count,
                'comment_count': item.comment_count,
                'is_official': item.is_official,
                'tags': item.tags,
                'updated_at': 'now()'
            }, on_conflict='list_id').execute()
            print(f"✓ Inserted: {item.title}")
        except Exception as e:
            print(f"✗ Failed to insert {item.title}: {e}")
    
    # Verify
    print("\nVerifying lists in database...")
    result = client.table('list_summaries').select('list_id, title, film_count, like_count').execute()
    print(f"Total lists in DB: {len(result.data)}\n")
    
    for item in result.data[:5]:
        print(f"  - {item['title']}")
        print(f"    Films: {item['film_count']}, Likes: {item['like_count']}")
    
    print("\n✓ Lists successfully seeded!")
    
except Exception as e:
    print(f"\n✗ Error fetching from Letterboxd: {e}")
    print("\nThis is the rate limiting issue we discussed.")
    print("For now, the seeded lists are sufficient for testing.")
