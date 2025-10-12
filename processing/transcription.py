# pip install -U pytubefix python-dotenv
import os
import subprocess
import math
import tempfile
from pytubefix import YouTube
from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()
from pathlib import Path
import xml.etree.ElementTree as ET
import html, re
from typing import List, Dict, Tuple, Optional

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# =========================
# УТИЛИТЫ ЯЗЫК/КАПШНЫ
# =========================
def _norm_lang(code: str) -> str:
    c = code[2:] if code.startswith("a.") else code
    return c.split("-")[0].lower()

def _caption_pairs(captions) -> List[Tuple[str, object]]:
    pairs = []
    try:
        for k, v in captions.items():
            code = k if isinstance(k, str) else getattr(v, "code", None) or getattr(k, "code", None)
            if isinstance(code, str):
                pairs.append((code, v))
    except Exception:
        for obj in captions:
            code = getattr(obj, "code", None) or getattr(obj, "name", None)
            if isinstance(code, str):
                pairs.append((code, obj))
    # убрать дубликаты, сохраняя порядок
    seen, out = set(), []
    for code, cap in pairs:
        if code not in seen:
            out.append((code, cap)); seen.add(code)
    return out

def _detect_spoken_lang(pairs: List[Tuple[str, object]]) -> Optional[str]:
    langs_present = {_norm_lang(code) for code, _ in pairs}
    for lang in ("ru", "uk", "en"):
        if lang in langs_present:
            return lang
    return None

def _pick_caption(yt) -> Tuple[Optional[object], Optional[str]]:
    pairs = _caption_pairs(yt.captions)
    if not pairs:
        return None, None

    manual = [(c, cap) for c, cap in pairs if not c.startswith("a.")]
    auto   = [(c, cap) for c, cap in pairs if c.startswith("a.")]
    langs_manual = {_norm_lang(c): (c, cap) for c, cap in manual}
    langs_auto   = {_norm_lang(c): (c, cap) for c, cap in auto}

    spoken = _detect_spoken_lang(pairs)
    tried = set()

    def try_pick(lang: str) -> Optional[Tuple[object, str]]:
        # сначала ручные, потом авто
        if lang in langs_manual and langs_manual[lang][0] not in tried:
            c, cap = langs_manual[lang]; tried.add(c); return cap, c
        if lang in langs_auto and langs_auto[lang][0] not in tried:
            c, cap = langs_auto[lang]; tried.add(c); return cap, c
        return None

    # 1) язык речи — ручные -> авто
    if spoken:
        r = try_pick(spoken)
        if r: return r

    # 2) ru -> uk -> en (пропуская spoken, если он уже пробован)
    for lang in ("ru", "uk", "en"):
        if lang == spoken:
            continue
        r = try_pick(lang)
        if r: return r

    # 3) любые прочие: ручные, затем авто
    for c, cap in manual + auto:
        if c not in tried:
            return cap, c

    return None, None

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
            rs = min(rs, duration)
            re_ = min(re_, duration)

        rounded.append({"start": rs, "end": min(re_, int(duration) + 0.0), "text": s["text"]})

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
    yt = YouTube(url)
    if not yt.captions:
        raise RuntimeError("Субтитры не найдены.")

    cap, chosen_code = _pick_caption(yt)
    if not cap:
        raise RuntimeError(f"Подходящая дорожка не найдена. Доступные: "
                           f"{[_c for _c, _ in _caption_pairs(yt.captions)]}")

    srt_text = cap.generate_srt_captions()
    segs = _srt_to_segments(srt_text)
    if not segs:
        raise RuntimeError("Получены пустые субтитры после фильтрации ремарок.")
    return segs, chosen_code

# =========================
# WHISPER
# =========================
MAX_AUDIO_DURATION_SECONDS = 1200  # 20 мин под лимит ~25MB

def get_audio_duration(audio_path):
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

def transcribe_via_whisper(audio_path) -> List[Dict[str, float]]:
    file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    audio_duration = get_audio_duration(audio_path)
    if audio_duration is None:
        raise Exception(f"Не удалось определить длительность аудио: {audio_path}")

    full_transcript: List[Dict[str, float]] = []

    def _append_segments(segments, offset=0.0):
        for seg in segments:
            full_transcript.append({
                "start": float(seg.start) + offset,
                "end": float(seg.end) + offset,
                "text": str(seg.text).strip()
            })

    if file_size_mb > 24 or audio_duration > MAX_AUDIO_DURATION_SECONDS:
        num_chunks = math.ceil(audio_duration / MAX_AUDIO_DURATION_SECONDS)
        for i in range(num_chunks):
            print(f"Транскрибируем часть {i+1}/{num_chunks}...")
            start_time = i * MAX_AUDIO_DURATION_SECONDS
            end_time = min((i + 1) * MAX_AUDIO_DURATION_SECONDS, audio_duration)

            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_file:
                chunk_path = tmp_file.name

            cmd = [
                "ffmpeg", "-i", str(audio_path),
                "-ss", str(start_time), "-to", str(end_time),
                "-ac", "1", "-ar", "24000",
                "-c:a", "libopus", "-b:a", "32k",
                "-y", chunk_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                os.remove(chunk_path)
                raise Exception(f"ffmpeg error: {result.stderr}")

            with open(chunk_path, "rb") as f:
                chunk_transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    response_format="verbose_json"
                )
            os.remove(chunk_path)
            _append_segments(chunk_transcript.segments, offset=start_time)
    else:
        with open(audio_path, "rb") as f:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="verbose_json"
            )
        _append_segments(transcript.segments, offset=0.0)

    return full_transcript

# =========================
# ЕДИНАЯ ТОЧКА: ПОЛУЧИТЬ СЕГМЕНТЫ И ЗАПИСАТЬ SRT
# =========================
def get_transcript_segments_and_file(url, audio_path="audio_only.ogg", out_dir="", force_whisper=False) -> Tuple[List[Dict[str, any]], str]:
    """
    Возвращает сегменты [{start,end,text}] и ПРИ ЭТОМ создаёт файл captions.txt
    одинаковым способом для обоих источников (YouTube/Whisper).
    """
    segments: List[Dict[str, float]] = []
    audio_duration = get_audio_duration(audio_path)
    chosen_code = None

    if force_whisper:
        segments = transcribe_via_whisper(audio_path)
    else:
        try:
            segments, chosen_code = download_captions_from_youtube(url)
            print(f"Выбрана дорожка: {chosen_code}")
        except Exception as e:
            print(f"Не удалось получить субтитры с YouTube ({e}). Пытаемся через Whisper.")
            raise "Скачать субтитры не получилось"
            # segments = transcribe_via_whisper(audio_path)
            
    segments = normalize_segments(segments, duration=audio_duration)

    # ЕДИНАЯ запись в TXT (нормализация внутри write_captions_file)
    write_captions_file(segments, filename=(Path(out_dir) / "captions.txt"))

    # Вернём уже нормализованные сегменты, чтобы совпадали с тем, что в файле
    return segments, chosen_code.replace("a.", "") if chosen_code else "ru"

# ==== запуск ====
if __name__ == "__main__":
    url = "https://www.youtube.com/watch?v=oemNf96Q3Go&t=635s" #"https://www.youtube.com/watch?v=2IaQdDjxViU"
    segments, lang_code = get_transcript_segments_and_file(url, force_whisper=False, audio_path="audio_only.ogg")
    print(segments[:5], lang_code)