import os
import httpx
from dotenv import load_dotenv

load_dotenv(".env")
cookie = os.getenv("LETTERBOXD_SESSION_COOKIE")

with httpx.Client(cookies={"letterboxd.user.CURRENT": cookie}) as client:
    r = client.get("https://letterboxd.com/settings/")
    html = r.text
    
    with open("settings_html.txt", "w", encoding="utf-8") as f:
        f.write(html)
    
    print(f"Written {len(html)} bytes")
