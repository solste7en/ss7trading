"""
auth.py — Schwab OAuth token management

First-time setup:  python auth.py
  → Opens your browser to Schwab login
  → Redirects back to localhost after you approve
  → Saves token.json for all future use (auto-refreshes)

Subsequent runs: just import get_client() — no browser needed.
"""
import sys
from pathlib import Path
import schwab
from config import APP_KEY, APP_SECRET, CALLBACK_URL, TOKEN_PATH


def get_client():
    """
    Return an authenticated Schwab client.
    Uses the saved token if available; otherwise raises to prompt first-time setup.
    """
    token_path = Path(TOKEN_PATH)
    if not token_path.exists():
        raise FileNotFoundError(
            f"No token found at {TOKEN_PATH}. "
            "Run  python auth.py  first to complete the one-time OAuth login."
        )
    return schwab.auth.client_from_token_file(
        token_path=str(token_path),
        api_key=APP_KEY,
        app_secret=APP_SECRET,
    )


def run_first_time_login():
    """
    Interactive OAuth flow. Opens a browser, handles the callback on
    https://127.0.0.1:8182/callback, and writes token.json.
    Only needed once — tokens auto-refresh after that.
    """
    print("=" * 60)
    print("Schwab OAuth — First-Time Login")
    print("=" * 60)
    print(f"  App Key:      {APP_KEY[:8]}...")
    print(f"  Callback URL: {CALLBACK_URL}")
    print(f"  Token path:   {TOKEN_PATH}")
    print()
    print("A browser window will open. Log in to Schwab and approve")
    print("access for your app. The callback is handled automatically.")
    print("=" * 60)

    schwab.auth.client_from_login_flow(
        api_key=APP_KEY,
        app_secret=APP_SECRET,
        callback_url=CALLBACK_URL,
        token_path=TOKEN_PATH,
    )
    print()
    print("✅ Token saved to", TOKEN_PATH)
    print("You can now run the web app:  python app.py")


if __name__ == "__main__":
    run_first_time_login()
