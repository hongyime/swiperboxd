import os
import traceback
from dotenv import load_dotenv

load_dotenv()

# Test loading auth service
try:
    from src.api.auth import get_auth_service
    svc = get_auth_service()
    print('AuthService created successfully')
    print(f'Supabase URL: {svc.supabase_url[:30] if svc.supabase_url else "None"}...')
    print(f'JWT Secret: {"set" if svc.supabase_jwt_secret else "None"}')
except Exception as e:
    print(f'Error: {type(e).__name__}: {e}')
    traceback.print_exc()
