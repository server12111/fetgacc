import os
from dotenv import load_dotenv

load_dotenv()

bot_token = os.getenv("BOT_TOKEN", "")
api_id = int(os.getenv("API_ID", "0"))
api_hash = os.getenv("API_HASH", "")
crypto_bot_token = os.getenv("CRYPTO_BOT_TOKEN", "")

OWNERS = [int(x) for x in os.getenv("OWNERS", "").split(",") if x.strip()]
SPONSORS = []

LZT_TOKEN = os.getenv("LZT_TOKEN", "")
LZT_MARKUP = float(os.getenv("LZT_MARKUP", "1.5"))
LZT_MIN_PRICE = float(os.getenv("LZT_MIN_PRICE", "0.5"))
LZT_MAX_PRICE = float(os.getenv("LZT_MAX_PRICE", "5.0"))
LZT_PAGE_SIZE = int(os.getenv("LZT_PAGE_SIZE", "10"))

TON_ADDRESS = os.getenv("TON_ADDRESS", "")
