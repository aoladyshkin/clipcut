# Conversion rates
RUB_TO_USDT_RATE = 1/85  # 1 USDT = 85 RUB
RUB_TO_STARS_RATE = 100/180  # 100 Stars = 180 RUB



PACKAGES = [
    {"generations": 1, "rub": 149, "discount_rub": 99, "rub_per_generation": 0, "discount_rub_per_generation": 0, "highlight": False},
    {"generations": 5, "rub": 449, "discount_rub": 349, "rub_per_generation": 0, "discount_rub_per_generation": 0, "highlight": False},
    {"generations": 10, "rub": 799, "discount_rub": 549, "rub_per_generation": 0, "discount_rub_per_generation": 0, "highlight": True},
    {"generations": 25, "rub": 1790, "discount_rub": 1049, "rub_per_generation": 0, "discount_rub_per_generation": 0, "highlight": False},
    {"generations": 50, "rub": 2590, "discount_rub": 1899, "rub_per_generation": 0, "discount_rub_per_generation": 0, "highlight": False},
]

def get_package_prices(discount_active: bool = False, referral_discount_active: bool = False) -> list:
    
    """Returns a list of all packages with their calculated prices."""
    prices = []
    for pkg in PACKAGES:
        rub_price = pkg["discount_rub"] if (discount_active or referral_discount_active) and "discount_rub" in pkg else pkg["rub"]

        price_per_item = pkg.get("discount_rub_per_generation") if (discount_active or referral_discount_active) and "discount_rub_per_generation" in pkg else pkg.get("rub_per_generation")
        prices.append({
            "generations": pkg["generations"],
            "rub": rub_price,
            "usdt": round(rub_price * RUB_TO_USDT_RATE, 2),
            "stars": int(rub_price * RUB_TO_STARS_RATE),
            "original_rub": pkg["rub"],
            "highlight": pkg.get("highlight", False),
            "price_per_generation": price_per_item
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