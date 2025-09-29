import os
from dotenv import load_dotenv

load_dotenv()

FEEDBACK_GROUP_ID = os.environ.get("FEEDBACK_GROUP_ID")

# config.py

REGULAR_PRICES = {
    "crypto_price_per_short": 1.0,
    "crypto_discounts": {
        25: 0.05,  # 5% discount for 25 or more shorts
        50: 0.1   # 10% discount for 50 or more shorts
    },
    "stars_packages": [
        {"shorts": 1, "stars": 59},
        {"shorts": 3, "stars": 149},
        {"shorts": 5, "stars": 299},
        {"shorts": 10, "stars": 499},
        {"shorts": 25, "stars": 1199},
        {"shorts": 50, "stars": 1749},
        {"shorts": 70, "stars": 2099},
        {"shorts": 100, "stars": 2499},
    ]
}

TUTORIAL_LINK = "https://telegra.ph/Kak-sdelat-virusnyj-Shorts-za-5-minut-09-20"

# --- Discount Prices ---

DISCOUNT_PRICES = {
    "crypto_price_per_short": 0.8,
    "crypto_discounts": {
        10: 0.01,
        25: 0.03,
        50: 0.05
    },
    "stars_packages": [
        {"shorts": 1, "stars": 47},
        {"shorts": 3, "stars": 119},
        {"shorts": 5, "stars": 239},
        {"shorts": 10, "stars": 399},
        {"shorts": 25, "stars": 959},
        {"shorts": 50, "stars": 1399},
        {"shorts": 70, "stars": 1679},
        {"shorts": 100, "stars": 1999},
    ]
}

DEMO_CONFIG = {
    "url": "https://youtu.be/4_3VXLK_K_A?si=GVZ3IySlOPK09Ohc",
    "config": {
        "force_ai_transcription": False,
        'shorts_number': 'auto',
        'layout': 'face_track_9_16',
        'bottom_video': None,
        'subtitles_type': 'word-by-word',
        'subtitle_style': 'white',
        'capitalize_sentences': False
    },
    'video_message_params': [
        {'start': '00:00:00', 'end': '00:00:00', 'hook': 'demo_hook1' },
        {'start': '00:00:01', 'end': '00:00:00', 'hook': 'demo_hook2' },
        {'start': '00:00:02', 'end': '00:00:00', 'hook': 'demo_hook3' }
    ]
}