import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

movies = supabase.table("movies").select("slug", count="exact").execute()
lists = supabase.table("lists").select("list_id", count="exact").execute()
list_memberships = supabase.table("list_memberships").select("list_id", count="exact").execute()
user_interactions = supabase.table("user_interactions").select("user_id", count="exact").execute()

print(f"Movies: {movies.count}")
print(f"Lists: {lists.count}")
print(f"List Memberships: {list_memberships.count}")
print(f"User Interactions: {user_interactions.count}")
