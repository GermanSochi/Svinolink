"""Simulate Render startup: run main.py but stop before actual bot start."""
import sys
import os

# Force simulating Render env
os.environ.setdefault("RENDER", "true")

import traceback

try:
    # Step 1: Import everything main.py imports
    print("1. Importing config...")
    from config import settings
    print(f"   OK: bot_token={'SET' if settings.bot_token else 'EMPTY'}, is_render={settings.is_render}")
    print(f"   instagram_is_active={settings.instagram_is_active()}")
except Exception as e:
    print("   FAILED")
    traceback.print_exc()
    sys.exit(1)

try:
    print("2. Importing main...")
    import main
    print("   OK")
except Exception as e:
    print("   FAILED")
    traceback.print_exc()
    sys.exit(1)

try:
    print("3. Building dispatcher...")
    dp = main._build_dispatcher()
    print("   OK")
except Exception as e:
    print("   FAILED")
    traceback.print_exc()
    sys.exit(1)

try:
    print("4. Importing server_runner...")
    from server_runner import build_app, run_polling_with_http, run_webhook_mode
    print("   OK")
except Exception as e:
    print("   FAILED")
    traceback.print_exc()
    sys.exit(1)

try:
    print("5. Importing instagram_download (the heavy one)...")
    from instagram_download import init_instagram_downloader, download_instagram_video, instagram_user_message
    print("   OK")
    print("   instagram_user_message():", instagram_user_message()[:80])
except Exception as e:
    print("   FAILED")
    traceback.print_exc()
    sys.exit(1)

try:
    print("6. Importing all remaining modules...")
    import admin_panel
    import bot_startup
    import middleware_log
    import store
    import bot_stats
    import bot_messages
    import admin_auth
    import instagram_anti_detection
    import instagram_urls
    import message_urls
    print("   ALL OK")
except Exception as e:
    print("   FAILED")
    traceback.print_exc()
    sys.exit(1)

print("\n=== ALL STARTUP CHECKS PASSED ===")
print("Bot should work. If Render fails, it might be a Docker build issue, not code.")
