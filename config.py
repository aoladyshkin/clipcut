import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# --- Telegram Bot ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_GROUP_ID = os.environ.get("ADMIN_GROUP_ID")
MODERATORS_GROUP_ID = os.environ.get("MODERATORS_GROUP_ID")
ADMIN_USER_TAG = os.environ.get("ADMIN_USER_TAG", "@aoladyshkin")
MODERATORS_USER_TAGS = os.environ.get("MODERATORS_USER_TAGS", "")
ADMIN_USER_IDS = [id.strip() for id in os.environ.get("ADMIN_USER_IDS", "").split(',')]
FEEDBACK_GROUP_ID = os.environ.get("FEEDBACK_GROUP_ID")
FORWARD_RESULTS_GROUP_ID = os.environ.get("FORWARD_RESULTS_GROUP_ID")
MAX_CONCURRENT_TASKS = int(os.environ.get("MAX_CONCURRENT_TASKS", "1"))
REWARD_FOR_FEEDBACK = 10
FREE_SHORTS_ON_START = 5

# --- OpenAI ---
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# --- Video Processing ---
DELETE_OUTPUT_AFTER_SENDING = os.environ.get("DELETE_OUTPUT_AFTER_SENDING", "false").lower() == "true"
PROJECT_ROOT = Path(__file__).parent
KEEPERS_DIR = PROJECT_ROOT / "keepers"
HAARCASCADE_FRONTALFACE_DEFAULT = str(PROJECT_ROOT / "haarcascade_frontalface_default.xml")
HAARCASCADE_PROFILEFACE = str(PROJECT_ROOT / "haarcascade_profileface.xml")
CONFIG_EXAMPLES_DIR = PROJECT_ROOT / "config_examples"
DEMO_SHORTS_DIR = PROJECT_ROOT / "demo_shorts"
YOUTUBE_COOKIES_FILE = os.environ.get("YOUTUBE_COOKIES_FILE")

VIDEO_MAP = {
    'gta': str(KEEPERS_DIR / 'gta.mp4'),
    'minecraft': str(KEEPERS_DIR / 'minecraft_parkur.mp4')
}

# --- Tutorial ---
TUTORIAL_LINK = "https://telegra.ph/Kak-sdelat-virusnyj-Shorts-za-5-minut-09-20"

# --- Database ---
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/database.db")

# --- Analytics ---
ANALYTICS_DATABASE_URL = os.environ.get("ANALYTICS_DATABASE_URL", "sqlite:///./data/analytics.db")
CRYPTO_BOT_TOKEN = os.environ.get("CRYPTO_BOT_TOKEN")

# --- YooKassa ---
YOOKASSA_SHOP_ID = os.environ.get("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.environ.get("YOOKASSA_SECRET_KEY")

FREESPACE_LIMIT_MB = 200  # Minimum free space in MB required to process videos