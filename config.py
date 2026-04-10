"""
config.py — loads Schwab API credentials from .env
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# All paths are relative to this file's directory so it works
# regardless of where you launch the script from
BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

APP_KEY       = os.environ["SCHWAB_APP_KEY"]
APP_SECRET    = os.environ["SCHWAB_APP_SECRET"]
CALLBACK_URL  = os.environ.get("SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182/callback")
TOKEN_PATH    = str(BASE_DIR / os.environ.get("SCHWAB_TOKEN_PATH", "token.json"))
DB_PATH       = BASE_DIR.parent / "trades.db"
