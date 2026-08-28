import os, sys
os.environ["INSTAGRAM_COOKIES_JSON"] = "sessionid=14764752993%3Ajyb8nk6iSEArSM%3A22%3AAYiuoFAqoh_jtDEfhQmt6OPhTL1aR20tUxGwGnapyg|ds_user_id=14764752993|csrftoken=cs6JvdBhTr9QBUJdYuHFw9ErsB18DGlL|mid=aevS2wALAAEBoLP3qqAyZXxhT2F8|ig_did=CEC5A5BF-F777-4D20-BEC3-11DEFA4BC4C3|datr=PfLtaWqI_mw9uqDi5Cm5ZesE"

from instagram_download import (
    _load_cookies_from_env, _apply_env_cookies, _new_instagram_client,
    _load_cookies_dict, _download_via_private_api, _extract_shortcode,
    _shortcode_to_media_id
)

url = "https://www.instagram.com/stories/snowboarding_sucks/3935441032632448198"

print("=== Cookie test ===")
cookies = _load_cookies_from_env()
print(f"Cookies loaded: {len(cookies)} keys")
print(f"sessionid: {cookies.get('sessionid', 'MISSING')[:30]}...")

print("\n=== Private API test ===")
result = _download_via_private_api(url)
print(f"Private API result: {result}")

print("\n=== instagrapi client test ===")
cl = _new_instagram_client()
_apply_env_cookies(cl)
print(f"Client user_id: {cl.user_id}")

if cl.user_id:
    try:
        media_pk = cl.media_pk_from_url(url)
        print(f"media_pk: {media_pk}")
    except Exception as e:
        print(f"media_pk error: {e}")

print("\n=== Direct cookie check ===")
d = _load_cookies_dict()
print(f"Direct dict: sessionid={'YES' if d.get('sessionid') else 'NO'}")
