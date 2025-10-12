

# --- Regular Prices ---

REGULAR_PRICES = {
    "crypto_price_per_short": 1.0,
    "crypto_discounts": {
        10: 0.08,
        25: 0.15,  # 5% discount for 25 or more shorts
        50: 0.37,   # 10% discount for 50 or more shorts
        70: 0.45,
        100: 0.55,
    },
    "stars_packages": [
        {"shorts": 1, "stars": 59},
        {"shorts": 3, "stars": 169},
        {"shorts": 5, "stars": 264},
        {"shorts": 10, "stars": 499},
        {"shorts": 25, "stars": 1099},
        {"shorts": 50, "stars": 1749},
        {"shorts": 70, "stars": 2099},
        {"shorts": 100, "stars": 2499},
    ]
}

# --- Discount Prices ---

DISCOUNT_PRICES = {
    "crypto_price_per_short": 0.8,
    "crypto_discounts": {
        10: 0.08,
        25: 0.15,
        50: 0.38,
        70: 0.47,
        100: 0.57,
    },
    "stars_packages": [
        {"shorts": 1, "stars": 47},
        {"shorts": 3, "stars": 134},
        {"shorts": 5, "stars": 209},
        {"shorts": 10, "stars": 399},
        {"shorts": 25, "stars": 849},
        {"shorts": 50, "stars": 1399},
        {"shorts": 70, "stars": 1679},
        {"shorts": 100, "stars": 1899},
    ]
}

DEMO_CONFIG = {
    "url": "https://www.youtube.com/watch?v=zPrwTpo4TiM",
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
        { "start": '00:58:49', "end": "00:59:35", "hook": "Деньги должны стать божеством" },
        { "start": '01:12:38', "end": "01:13:12", "hook": "Сложная миссия - Николай Василенко о своей водке" },
        { "start": '01:13:49', "end": "01:14:39", "hook": "Производитель - посредник между человеком и государством" },
    ]
}