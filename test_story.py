import os
os.environ["INSTAGRAM_COOKIES_JSON"] = "sessionid=14764752993%3Ajyb8nk6iSEArSM%3A22%3AAYiuoFAqoh_jtDEfhQmt6OPhTL1aR20tUxGwGnapyg|ds_user_id=14764752993|csrftoken=cs6JvdBhTr9QBUJdYuHFw9ErsB18DGlL"

import requests
from instagram_download import _load_cookies_from_env

cookies = _load_cookies_from_env()
print("Cookies loaded:", len(cookies))

headers = {
    "User-Agent": "Instagram 275.0.0.27.98 Android",
    "X-IG-App-ID": "936619743392459",
}

# Story URL - numeric ID should be used directly as media_id
story_id = "3935441032632448198"

# Try direct API with numeric ID
api_url = f"https://i.instagram.com/api/v1/media/{story_id}/info/"
resp = requests.get(api_url, headers=headers, cookies=cookies, timeout=10)
print(f"Direct API status: {resp.status_code}")
print(f"Response: {resp.text[:300]}")
