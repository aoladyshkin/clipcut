import json
from pathlib import Path

LOCALES_DIR = Path(__file__).parent / "locales"
translations = {}

def load_translations():
    for lang_file in LOCALES_DIR.glob("*.json"):
        lang = lang_file.stem
        with open(lang_file, "r", encoding="utf-8") as f:
            translations[lang] = json.load(f)

def get_translation(lang_code: str, key: str) -> str:
    return translations.get(lang_code, translations["en"]).get(key, key)

load_translations()
