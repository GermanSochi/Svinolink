"""Test startup: simulate what Render does."""
import traceback, sys

try:
    print("1. Importing main...")
    import main
    print("   OK")
except Exception as e:
    print("   FAILED at import main")
    traceback.print_exc()
    sys.exit(1)

try:
    print("2. Building dispatcher...")
    dp = main._build_dispatcher()
    print("   OK")
except Exception as e:
    print("   FAILED building dispatcher")
    traceback.print_exc()
    sys.exit(1)

try:
    print("3. Importing server_runner...")
    from server_runner import build_app, run_polling_with_http, run_webhook_mode
    print("   OK")
except Exception as e:
    print("   FAILED importing server_runner")
    traceback.print_exc()
    sys.exit(1)

try:
    print("4. Simulating server_runner build_app (no bot)...")
    # Just check imports, not actual app creation
    from bot_startup import configure_bot
    from instagram_download import init_instagram_downloader
    print("   OK — all startup imports resolved")
except Exception as e:
    print("   FAILED simulating startup")
    traceback.print_exc()
    sys.exit(1)

print("\n=== ALL STARTUP CHECKS PASSED ===")
