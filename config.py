# config.py

# Pricing for CryptoBot (in USDT)
CRYPTO_PRICE_PER_SHORT = 0.1
CRYPTO_DISCOUNTS = {
    25: 0.05,  # 10% discount for 25 or more shorts
    50: 0.1   # 20% discount for 50 or more shorts
    
}

# Pricing for Telegram Stars
STARS_PACKAGES = [
    {"shorts": 1, "stars": 59},
    {"shorts": 3, "stars": 149},
    {"shorts": 5, "stars": 299},
    {"shorts": 10, "stars": 499},
    {"shorts": 25, "stars": 1199},
    {"shorts": 50, "stars": 1749},
    {"shorts": 70, "stars": 2099},
    {"shorts": 100, "stars": 2499},
]

TUTORIAL_LINK = "https://telegra.ph/Kak-sdelat-virusnyj-Shorts-za-5-minut-09-20"