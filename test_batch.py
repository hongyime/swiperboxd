import os
import sys
from dotenv import load_dotenv

load_dotenv()

from src.api.store import SupabaseStore

def main():
    store = SupabaseStore()
    print("Testing batch_add_watchlist...")
    try:
        slugs = ["fight-club", "the-matrix", "inception"]
        result = store.batch_add_watchlist("test_user_123", slugs)
        print("Result:", result)
    except Exception as e:
        print("Exception:", e)

if __name__ == "__main__":
    main()
