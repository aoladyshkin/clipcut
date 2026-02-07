# pip install -U pytubefix python-dotenv
import os
import subprocess
import math
import tempfile
import yt_dlp
import logging
import pysubs2
from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)
from dotenv import load_dotenv
load_dotenv()
from pathlib import Path
import xml.etree.ElementTree as ET
import html, re
from typing import List, Dict, Tuple, Optional
from config import YOUTUBE_COOKIES_FILE

client = None # No longer using OpenAI API

_whisper_model = None

def get_whisper_model():
    """Initializes and returns a singleton WhisperModel instance."""
    global _whisper_model
    if _whisper_model is None:
        logger.info("Initializing Whisper model for the first time...")
        # Using "small" model. For better quality, "medium" can be used.
        _whisper_model = WhisperModel("small", device="cpu", compute_type="int8")
        logger.info("Whisper model initialized.")
    return _whisper_model


# =========================
# УТИЛИТЫ ЯЗЫК/КАПШНЫ
# =========================
def _pick_best_subtitle_yt_dlp(info_dict: dict) -> Tuple[Optional[str], bool]:
    """
    Выбирает лучший код языка и тип (авто/мануал) на основе логики приоритетов.
    Возвращает (lang_code, is_auto).
    """
    subs = info_dict.get('subtitles', {}) or {}
    auto_subs = info_dict.get('automatic_captions', {}) or {}

    manual_langs = set(subs.keys())
    auto_langs = set(auto_subs.keys())
    all_langs = manual_langs | auto_langs

    if not all_langs:
        return None, False

    # Нормализация для поиска (en-US -> en)
    def get_base_lang(code):
        return code.split('-')[0].lower()

    # 0. Определяем "разговорный" язык (если он есть в списке приоритетных)
    spoken = None
    for priority_lang in ("ru", "uk", "en"):
        if any(get_base_lang(l) == priority_lang for l in all_langs):
            spoken = priority_lang
            break

    def try_pick(target_base_lang: str) -> Tuple[Optional[str], bool]:
        # Сначала ищем в ручных
        for l in manual_langs:
            if get_base_lang(l) == target_base_lang:
                return l, False
        # Потом в авто
        for l in auto_langs:
            if get_base_lang(l) == target_base_lang:
                return l, True
        return None, False

    # 1. Приоритет: Spoken (Manual -> Auto)
    if spoken:
        code, is_auto = try_pick(spoken)
        if code: return code, is_auto

    # 2. Приоритет: ru -> uk -> en (Manual -> Auto)
    for lang in ("ru", "uk", "en"):
        if lang == spoken: continue
        code, is_auto = try_pick(lang)
        if code: return code, is_auto

    # 3. Любые ручные
    if manual_langs:
        return list(manual_langs)[0], False

    # 4. Любые авто
    if auto_langs:
        return list(auto_langs)[0], True

    return None, False

# =========================
# ФИЛЬТР РЕМАРОК
# =========================
_BRACKETED_RE = re.compile(r'^\s*[\\\[\(].*?[\\\]\)]\s*$', re.IGNORECASE | re.DOTALL)
_MUSIC_RE = re.compile(r'^[\s♪♫]+', re.UNICODE)
  
def _is_non_speech(text: str) -> bool:
    t = text.strip()
    return (not t) or _MUSIC_RE.match(t) or _BRACKETED_RE.match(t)

# =========================
# SRT -> СЕГМЕНТЫ
# =========================
def _srt_time_to_seconds(time_str):
    parts = time_str.split(',')
    h, m, s = map(int, parts[0].split(':'))
    ms = int(parts[1])
    return h * 3600 + m * 60 + s + ms / 1000

def _srt_to_segments(srt_text: str) -> List[Dict[str, float]]:
    segs: List[Dict[str, float]] = []
    for block in srt_text.strip().split('\n\n'):
        lines = block.split('\n')
        if len(lines) >= 3:
            time_line = lines[1]
            text_lines = lines[2:]
            
            try:
                start_str, end_str = time_line.split(' --> ')
                start = _srt_time_to_seconds(start_str)
                end = _srt_time_to_seconds(end_str)
                
                text = " ".join(text_lines).strip()
                if text and not _is_non_speech(text):
                    segs.append({"start": start, "end": end, "text": text})
            except ValueError:
                # Пропускаем невалидные блоки, если что-то пошло не так с разбором
                print(f"Не удалось разобрать SRT-блок: {block}")
                continue
    return segs

# =========================
# НОРМАЛИЗАЦИЯ СЕГМЕНТОВ
# =========================
def _clean_segment_text(text: str) -> str:
    """
    Убирает нежелательные символы из текста, оставляя только буквы
    (кириллические и латинские), цифры, пробелы и основную пунктуацию.
    """
    if not text:
        return ""
    # Этот регекс заменяет любой символ, который НЕ является буквой, цифрой,
    # пробелом или одним из разрешенных знаков препинания (.,!?), на пустую строку.
    # Это также решает проблему с ">> ".
    cleaned_text = re.sub(r'[^\w\s.,!?]', '', text)
    return cleaned_text.strip()


def normalize_segments(segs: List[Dict[str, float]], duration: Optional[float] = None) -> List[Dict[str, float]]:
    """
    Единая нормализация для обоих источников (YouTube/Whisper):
    - удаление ремарок
    - сортировка
    - устранение пересечений
    - ОКРУГЛЕНИЕ start/end до 0.1 cек
    - ОБРЕЗКА сегментов по длительности (если указана)
    """
    if not segs:
        return []

    # базовая очистка
    cleaned = []
    for s in segs:
        text = str(s.get("text", "")).strip()
        if not text or _is_non_speech(text):
            continue
        
        cleaned_text = _clean_segment_text(text)
        if not cleaned_text:
            continue

        start = float(s["start"])
        end = float(s["end"])
        cleaned.append({"start": start, "end": end, "text": cleaned_text})

    if not cleaned:
        return []

    # сортировка и устранение пересечений
    cleaned.sort(key=lambda x: (x["start"], x["end"]))
    for i in range(len(cleaned) - 1):
        curr, nxt = cleaned[i], cleaned[i + 1]
        if curr["end"] > nxt["start"]:
            curr["end"] = nxt["start"]
        if curr["end"] < curr["start"]:
            curr["end"] = curr["start"]
    if cleaned[-1]["end"] < cleaned[-1]["start"]:
        cleaned[-1]["end"] = cleaned[-1]["start"]

    # ОКРУГЛЕНИЕ до десятых секунды и ОБРЕЗКА
    rounded = []
    for s in cleaned:
        rs = round(s["start"] * 10) / 10.0
        re_ = round(s["end"] * 10) / 10.0
        # защита от инверсий после округления
        if re_ < rs:
            re_ = rs
        
        if duration is not None:
            if rs >= duration: 
                continue  # Пропускаем сегменты, которые начинаются после конца аудио
            re_ = min(re_, duration)

        rounded.append({"start": rs, "end": re_, "text": s["text"]})

    return rounded


# === заменяем функции форматирования/записи ===

def _fmt_seconds(seconds: float) -> str:
    """Секунды с одним знаком после запятой (ss.s)."""
    return f"{round(float(seconds), 1):.1f}"

def _to_caption_text(segs: List[Dict[str, float]]) -> str:
    """
    Без порядковых номеров. Каждая запись:
    ss.s --> ss.s
    текст

    Между блоками — пустая строка.
    """
    lines = []
    for seg in segs:
        a = _fmt_seconds(seg["start"])
        b = _fmt_seconds(seg["end"])
        lines.append(f"{a} --> {b}\n{seg['text']}\n")
    return "\n".join(lines).strip() + "\n"

def write_captions_file(segments: List[Dict[str, float]], filename: str = "captions.txt",) -> Path:
    """
    Пишем captions в TXT (совместимо с OpenAI Files API).
    Округление и устранение пересечений выполняются через normalize_segments().
    """
    txt = _to_caption_text(segments)
    out_path = Path(filename)
    out_path.write_text(txt, encoding="utf-8")
    print(f"\nСохранено в файл: {out_path.resolve()}")
    return out_path


# =========================
# ПОЛУЧЕНИЕ СЕГМЕНТОВ ИЗ YOUTUBE
# =========================
def download_captions_from_youtube(url: str) -> Tuple[List[Dict[str, float]], Optional[str]]:
    # 1. Получаем информацию о доступных субтитрах (без скачивания)
    ydl_opts_info = {
        'skip_download': True,
        'noplaylist': True,
        'quiet': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36',
        }
    }
    if YOUTUBE_COOKIES_FILE and os.path.exists(YOUTUBE_COOKIES_FILE):
        ydl_opts_info['cookiefile'] = YOUTUBE_COOKIES_FILE

    with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as e:
            raise RuntimeError(f"Ошибка получения инфо о видео: {e}")

    # 2. Выбираем лучшую дорожку по нашей логике
    chosen_code, is_auto = _pick_best_subtitle_yt_dlp(info)
    if not chosen_code:
        raise RuntimeError("Субтитры не найдены (ни ручные, ни авто).")

    # 3. Скачиваем выбранную дорожку
    with tempfile.TemporaryDirectory() as tmpdirname:
        out_tmpl = os.path.join(tmpdirname, 'subs')
        ydl_opts_down = {
            'skip_download': True,
            'writesubtitles': not is_auto,
            'writeautomaticsub': is_auto,
            'subtitleslangs': [chosen_code],
            'subtitlesformat': 'srt',
            'outtmpl': out_tmpl,
            'quiet': True,
            'noplaylist': True,
        }
        if YOUTUBE_COOKIES_FILE and os.path.exists(YOUTUBE_COOKIES_FILE):
            ydl_opts_down['cookiefile'] = YOUTUBE_COOKIES_FILE

        with yt_dlp.YoutubeDL(ydl_opts_down) as ydl:
            ydl.download([url])

        # Ищем скачанный файл (yt-dlp добавляет код языка в имя файла)
        files = [f for f in os.listdir(tmpdirname) if f.endswith('.srt')]
        if not files:
            raise RuntimeError(f"Не удалось скачать выбранные субтитры ({chosen_code}).")
        
        srt_path = os.path.join(tmpdirname, files[0])
        with open(srt_path, 'r', encoding='utf-8') as f:
            srt_text = f.read()

    segs = _srt_to_segments(srt_text)
    if not segs:
        raise RuntimeError("Получены пустые субтитры после парсинга.")
        
    return segs, chosen_code

# =========================
# WHISPER
# =========================
MAX_AUDIO_DURATION_SECONDS = 1200  # 20 мин под лимит ~25MB

def get_audio_duration(audio_path):
    if not audio_path or not os.path.exists(audio_path):
        return None
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None

def transcribe_via_faster_whisper(audio_path) -> List[Dict[str, float]]:
    """
    Transcribes the given audio file using the local faster-whisper model.
    """
    model = get_whisper_model()
    
    segments, _ = model.transcribe(
        str(audio_path),
        task="transcribe",
        word_timestamps=False, # We only need phrase-level timestamps here
        beam_size=1,
        best_of=1,
        temperature=0.0
    )

    # Convert generator to list of dicts
    transcript_list = []
    for segment in segments:
        transcript_list.append({
            "start": segment.start,
            "end": segment.end,
            "text": segment.text.strip()
        })
    
    logger.info(f"Transcription via faster-whisper complete for {audio_path}.")
    return transcript_list

def transcribe_with_word_timestamps(audio_path):
    """
    Transcribes audio with word-level timestamps using high quality settings.
    Returns list of Segment objects (from faster_whisper).
    """
    model = get_whisper_model()
    segments, _ = model.transcribe(
        str(audio_path),
        task="transcribe",
        word_timestamps=True,
        beam_size=5,
        best_of=5,
        temperature=0.0
    )
    return list(segments)

# =========================
# ЕДИНАЯ ТОЧКА: ПОЛУЧИТЬ СЕГМЕНТЫ И ЗАПИСАТЬ SRT
# =========================
def get_transcript_segments_and_file(url, audio_path="audio_only.ogg", out_dir="", force_whisper=False, is_twitch_clip=False) -> Tuple[List[Dict[str, any]], str]:
    """
    Возвращает сегменты [{start,end,text}] и ПРИ ЭТОМ создаёт файл captions.txt
    одинаковым способом для обоих источников (YouTube/Whisper).
    """
    segments: List[Dict[str, float]] = []
    audio_duration = get_audio_duration(audio_path)
    chosen_code = None

    if force_whisper or is_twitch_clip:
        # This path is now taken for Twitch clips and YouTube transcription fallbacks
        segments = transcribe_via_faster_whisper(audio_path)
    else:
        try:
            segments, chosen_code = download_captions_from_youtube(url)
            print(f"Выбрана дорожка: {chosen_code}")
        except Exception as e:
            print(f"Не удалось получить субтитры с YouTube: {e}")
            raise e
            
    segments = normalize_segments(segments, duration=audio_duration)

    if not is_twitch_clip:
        # ЕДИНАЯ запись в TXT (нормализация внутри write_captions_file)
        write_captions_file(segments, filename=(Path(out_dir) / "captions.txt"))

    # Вернём уже нормализованные сегменты, чтобы совпадали с тем, что в файле
    return segments, chosen_code.replace("a.", "") if chosen_code else "ru"

# ==== запуск ====
if __name__ == "__main__":
    url = "https://www.youtube.com/watch?v=oemNf96Q3Go&t=635s" #"https://www.youtube.com/watch?v=2IaQdDjxViU"
    segments, lang_code = get_transcript_segments_and_file(url, force_whisper=False, audio_path="audio_only.ogg")
    print(segments[:5], lang_code)