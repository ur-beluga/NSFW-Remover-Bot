import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SIGHTENGINE_API_USER = os.getenv("SIGHTENGINE_API_USER")
SIGHTENGINE_API_SECRET = os.getenv("SIGHTENGINE_API_SECRET")
DEFAULT_THRESHOLD = float(os.getenv("DEFAULT_THRESHOLD", 0.65))
DB_PATH = os.getenv("DB_PATH", "bot_data.db")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
