# config.py

# Pricing for CryptoBot (in USDT)
CRYPTO_PRICE_PER_SHORT = 0.1
CRYPTO_DISCOUNTS = {
    25: 0.1,  # 10% discount for 25 or more shorts
    50: 0.2   # 20% discount for 50 or more shorts
}

# Pricing for Telegram Stars
STARS_PACKAGES = [
    {"shorts": 5, "stars": 50},
    {"shorts": 10, "stars": 95},
    {"shorts": 25, "stars": 225},
    {"shorts": 50, "stars": 400},
]
