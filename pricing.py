# Conversion rates
RUB_TO_USDT_RATE = 1/85  # 1 USDT = 85 RUB
RUB_TO_STARS_RATE = 100/180  # 100 Stars = 180 RUB



PACKAGES = [
    {"shorts": 5, "rub": 139, "discount_rub": 109, "rub_per_short": 0, "discount_rub_per_short": 0, "highlight": False}, # 169
    {"shorts": 10, "rub": 269, "discount_rub": 159, "rub_per_short": 0, "discount_rub_per_short": 0, "highlight": False}, # 319
    {"shorts": 25, "rub": 649, "discount_rub": 379, "rub_per_short": 0, "discount_rub_per_short": 0, "highlight": True}, # 739
    {"shorts": 50, "rub": 1190, "discount_rub": 719, "rub_per_short": 0, "discount_rub_per_short": 0, "highlight": False}, # 1390
    {"shorts": 100, "rub": 1990, "discount_rub": 1199, "rub_per_short": 0, "discount_rub_per_short": 0, "highlight": False}, # 1990
]

def get_package_prices(discount_active: bool = False) -> list:
    
    """Returns a list of all packages with their calculated prices."""
    prices = []
    for pkg in PACKAGES:
        rub_price = pkg["discount_rub"] if discount_active and "discount_rub" in pkg else pkg["rub"]
        price_per_item = pkg.get("discount_rub_per_short") if discount_active and "discount_rub_per_short" in pkg else pkg.get("rub_per_short")
        prices.append({
            "shorts": pkg["shorts"],
            "rub": rub_price,
            "usdt": round(rub_price * RUB_TO_USDT_RATE, 2),
            "stars": int(rub_price * RUB_TO_STARS_RATE),
            "original_rub": pkg["rub"],
            "highlight": pkg.get("highlight", False),
            "price_per_item": price_per_item
        })
    return prices

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