"""
config.py — loads Schwab API credentials from .env
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# ROOT_DIR is the project root (parent of core/) so paths resolve correctly
# regardless of where you launch the script from
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

APP_KEY       = os.environ["SCHWAB_APP_KEY"]
APP_SECRET    = os.environ["SCHWAB_APP_SECRET"]
CALLBACK_URL  = os.environ.get("SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182/callback")
TOKEN_PATH    = str(ROOT_DIR / os.environ.get("SCHWAB_TOKEN_PATH", "token.json"))
DB_PATH       = ROOT_DIR.parent / "trades.db"
