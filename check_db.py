import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

movies = supabase.table("movies").select("slug", count="exact").execute()
lists = supabase.table("list_summaries").select("list_id", count="exact").execute()
list_memberships = supabase.table("list_memberships").select("list_id", count="exact").execute()
watchlist = supabase.table("watchlist").select("user_id", count="exact").execute()
diary = supabase.table("diary").select("user_id", count="exact").execute()

print(f"Total Movies Cached: {movies.count}")
print(f"Total Lists Cached: {lists.count}")
print(f"Total Movies in Lists: {list_memberships.count}")
print(f"Your Watchlist Count: {watchlist.count}")
print(f"Your Diary Count: {diary.count}")
