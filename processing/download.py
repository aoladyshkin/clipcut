# -*- coding: utf-8 -*- 

import os
import subprocess
import re
import shutil
import json
import yt_dlp
from pathlib import Path
from pytubefix import YouTube
from config import YOUTUBE_COOKIES_FILE, FREESPACE_LIMIT_MB


import logging

logger = logging.getLogger(__name__)

from localization import get_translation

def _get_yt_dlp_command(base_command):
    if YOUTUBE_COOKIES_FILE and os.path.exists(YOUTUBE_COOKIES_FILE):
        # Insert --cookies after the initial 'python -m yt_dlp' part
        return base_command[:3] + ["--cookies", YOUTUBE_COOKIES_FILE] + base_command[3:]
    return base_command

def check_video_availability(url: str, lang: str = 'ru') -> (bool, str, str):
    """
    Checks if a YouTube video is available, has subtitles, and if there is enough disk space.
    First tries pytubefix for availability and subtitles, then falls back to yt-dlp.
    Returns a tuple (is_available, message, error_log).
    """
    # 0. Check for disk space
    free_space_mb = shutil.disk_usage('.').free / (1024 * 1024)
    if free_space_mb < FREESPACE_LIMIT_MB:
        return False, get_translation(lang, "not_enough_disk_space"), "not enough disk space"

    # 1. Check for video availability and subtitles
    try:
        # First, try with pytubefix
        yt = YouTube(url)
        _ = yt.title
        if not yt.streams:
            return False, get_translation(lang, "no_streams_found"), "no streams"
        
        # Check for any available captions
        if not yt.captions:
            return False, get_translation(lang, "subtitles_not_found"), "субтитры недоступны"

    except Exception as e:
        logger.warning(f"pytubefix failed to check video availability or subtitles: {e}. Falling back to yt-dlp.")
        # If pytubefix fails, try with yt-dlp for both checks
        is_available_yt_dlp, message_yt_dlp, err_yt_dlp = _check_video_availability_yt_dlp(url, lang)
        if not is_available_yt_dlp:
            return False, message_yt_dlp, err_yt_dlp
        
        # Subtitle check with yt-dlp as a fallback
        if not _check_subtitles_availability_yt_dlp(url):
            return False, get_translation(lang, "subtitles_not_found"), "субтитры недоступны"

    # 2. If all checks passed, return success
    return True, get_translation(lang, "video_available"), "Video is available"

def _check_subtitles_availability_yt_dlp(url: str) -> bool:
    """
    Checks if subtitles are available for a video using yt-dlp.
    """
    try:
        command = _get_yt_dlp_command(["python3", "-m", "yt_dlp", "--list-subs", "--skip-download", url])
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=20)
        # If yt-dlp finds subtitles, it will list them. If not, the output will be empty or show "no subtitles".
        if "Available subtitles" in result.stdout or "Available automatic captions" in result.stdout:
            return True
        return False
    except subprocess.CalledProcessError as e:
        # If the video is unavailable, yt-dlp might return an error, which is fine.
        # We are only interested in cases where we can check for subs.
        logger.warning(f"yt-dlp returned an error when checking for subtitles, but we proceed: {e.stderr}")
        # We can assume there are no subtitles if the command fails,
        # as availability is checked before this function is called.
        return False
    except Exception as e:
        logger.error(f"An unexpected error occurred while checking for subtitles with yt-dlp: {e}")
        return False

def _check_video_availability_yt_dlp(url: str, lang: str = 'ru') -> (bool, str, str):
    """
    Checks video availability using yt-dlp.
    """
    try:
        command = _get_yt_dlp_command(["python3", "-m", "yt_dlp", "--get-title", "--skip-download", url])
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=20)
        if result.stdout.strip():
            return True, get_translation(lang, "video_available"), "Video is available"
        else:
            return False, get_translation(lang, "unavailable_video_error"), "yt-dlp found no title"
    except subprocess.CalledProcessError as e:
        error_message = e.stderr.lower()
        if "age restricted" in error_message:
            return False, get_translation(lang, "age_restricted_error"), "age restricted"
        if "private" in error_message:
            return False, get_translation(lang, "private_video_error"), "private"
        if "unavailable" in error_message:
            return False, get_translation(lang, "unavailable_video_error"), error_message[:400]
        return False, get_translation(lang, "video_unavailable_check_link"), error_message
    except Exception as e:
        return False, get_translation(lang, "video_unavailable_check_link"), str(e)




from typing import List, Dict, Tuple, Optional, Set

def _get_available_audio_langs(url: str, yt_dlp_command: list) -> Set[str]:
    """Использует yt-dlp для получения списка всех доступных языков аудио."""
    try:
        command = yt_dlp_command + ["-F", url]
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=20)
        lines = result.stdout.split('\n')
        
        audio_langs = set()
        table_started = False
        for line in lines:
            if '---' in line: 
                table_started = True
                continue
            if not table_started:
                continue

            if 'audio only' in line:
                match = re.search(r'\[([a-zA-Z-]+)\]', line)
                if match:
                    audio_langs.add(match.group(1).split('-')[0])
        return audio_langs
    except Exception as e:
        print(f"Не удалось получить список аудио-языков через yt-dlp: {e}")
        return set()

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
    seen, out = set(), []
    for code, cap in pairs:
        if code not in seen:
            out.append((code, cap)); seen.add(code)
    return out

def _pick_lang_and_caption(yt, available_audio_langs: Set[str]) -> Tuple[Optional[object], Optional[str]]:
    """
    Выбирает дорожку субтитров, проверяя наличие соответствующей аудиодорожки.
    """
    pairs = _caption_pairs(yt.captions)
    if not pairs:
        return None, None

    manual = [(c, cap) for c, cap in pairs if not c.startswith("a.")]
    auto = [(c, cap) for c, cap in pairs if c.startswith("a.")]
    langs_manual = {_norm_lang(c): (c, cap) for c, cap in manual}
    langs_auto = {_norm_lang(c): (c, cap) for c, cap in auto}

    tried = set()

    def try_pick(lang: str) -> Optional[Tuple[object, str]]:
        if lang not in available_audio_langs:
            return None
        # Сначала ручные, потом авто
        if lang in langs_manual and langs_manual[lang][0] not in tried:
            c, cap = langs_manual[lang]; tried.add(c); return cap, c
        if lang in langs_auto and langs_auto[lang][0] not in tried:
            c, cap = langs_auto[lang]; tried.add(c); return cap, c
        return None

    # 1) Приоритетные языки: ru -> uk -> en
    for lang in ("ru", "uk", "en"):
        r = try_pick(lang)
        if r: return r

    # 2) Любые другие языки, где есть и аудио, и субтитры
    all_caption_langs = sorted(list(langs_manual.keys() | langs_auto.keys()))
    for lang in all_caption_langs:
        if lang not in ("ru", "uk", "en"):
            r = try_pick(lang)
            if r: return r

    return None, None

def _find_itag_for_lang_with_yt_dlp(url, lang: str, yt_dlp_command: list):
    print(f"Используем yt-dlp для поиска itag для языка '{lang}'...")
    try:
        command = yt_dlp_command + ["-F", url]
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=20)

        lines = result.stdout.split('\n')
        audio_streams = []
        table_started = False
        for line in lines:
            if '---' in line: table_started = True; continue
            if not table_started: continue

            if 'audio only' in line and f'[{lang}' in line:
                match = re.match(r'^\s*([\d-]+)\s+', line)
                if match:
                    itag = match.group(1)
                    tbr_match = re.search(r'(\d+)k', line)
                    bitrate = int(tbr_match.group(1)) if tbr_match else 0
                    audio_streams.append({'itag': itag, 'bitrate': bitrate})

        if not audio_streams:
            print("yt-dlp не нашел подходящих аудиопотоков.")
            return None

        best_stream = sorted(audio_streams, key=lambda x: x['bitrate'], reverse=True)[0]
        print(f"yt-dlp выбрал лучший по качеству itag: {best_stream['itag']}")
        return best_stream['itag']
        
    except Exception as e:
        print(f"Ошибка при вызове yt-dlp для получения форматов: {e}")
        return None

def download_video_segment(url: str, output_path: str, start_time: float, end_time: float):
    """
    Downloads a specific segment of a YouTube video using yt-dlp and ffmpeg.
    -ss is used as an input option for fast seeking.
    The segment is re-encoded to prevent frozen frames at the beginning.
    """
    output_path = str(output_path)
    duration = end_time - start_time

    ydl_opts = {
        'format': 'best[height<=1080][ext=mp4]/best[ext=mp4]',
        'outtmpl': output_path,
        'external_downloader': 'ffmpeg',
        'external_downloader_args': {
            'ffmpeg_i': [
                '-reconnect', '1',
                '-reconnect_streamed', '1',
                '-reconnect_delay_max', '5',
                '-ss', str(start_time)
            ],
            'default': [
                '-t', str(duration),
                '-c:v', 'libx264',
                '-preset', 'medium',
                '-crf', '20',
                '-c:a', 'aac',
                '-b:a', '192k',
                '-avoid_negative_ts', 'make_zero'
            ]
        }
    }

    if YOUTUBE_COOKIES_FILE and os.path.exists(YOUTUBE_COOKIES_FILE):
        ydl_opts['cookiefile'] = YOUTUBE_COOKIES_FILE

    try:
        print(f"Downloading segment from {start_time} to {end_time} using yt-dlp and ffmpeg...")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        print(f"Segment downloaded successfully to {output_path}")
        return output_path
        
    except Exception as e:
        # The original error message from yt-dlp can be verbose, let's log it but raise a cleaner one.
        error_message = str(e)
        logger.error(f"yt-dlp/ffmpeg failed to download segment: {error_message}", exc_info=True)
        # Re-raise with a more user-friendly message if needed, or just raise to propagate.
        raise

def download_audio_only(url, audio_path):
    """
    Автоматически определяет язык, проверяя наличие и аудио, и субтитров,
    скачивает наилучшее качество и имеет запасной вариант.
    """
    audio_path = Path(audio_path).with_suffix(".ogg")
    temp_path = audio_path.with_suffix(".temp.mp4")
    base_yt_dlp_command = _get_yt_dlp_command(["python3", "-m", "yt_dlp"])

    itag = None
    try:
        # 1. Получить список доступных аудио-языков
        available_audio_langs = _get_available_audio_langs(url, base_yt_dlp_command)
        if not available_audio_langs:
            print("Не найдено ни одной аудиодорожки через yt-dlp.")
        
        # 2. Выбрать язык, где есть и аудио, и субтитры
        yt = YouTube(url)
        _, chosen_code = _pick_lang_and_caption(yt, available_audio_langs)
        
        if chosen_code:
            lang = _norm_lang(chosen_code)
            print(f"Автоматически определен язык '{lang}' (субтитры: {chosen_code}). Ищем аудио...")
            itag = _find_itag_for_lang_with_yt_dlp(url, lang, base_yt_dlp_command)
        else:
            print("Не удалось найти язык, где есть и аудио, и субтитры.")

    except Exception as e:
        print(f"Ошибка при определении языка: {e}. Переключаемся на метод по умолчанию.")

    downloaded = False
    if itag:
        print(f"Попытка скачать аудио с itag={itag} с помощью yt-dlp...")
        try:
            command = base_yt_dlp_command + ["-f", itag, "--user-agent", "Mozilla/5.0", "-o", str(temp_path), url]
            subprocess.run(command, check=True, timeout=300)
            print("yt-dlp: Аудио успешно скачано.")
            downloaded = True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e_dlp:

            print(f"yt-dlp не смог скачать аудио с itag={itag}: {e_dlp}")

    # 3. Запасной метод: если ничего не вышло, качаем лучшее аудио через pytubefix
    if not downloaded:
        print("Переключаемся на pytubefix для скачивания аудио по умолчанию.")
        try:
            yt = YouTube(url)
            stream = yt.streams.filter(only_audio=True).order_by('abr').desc().first()
            if not stream:
                raise ConnectionError("pytubefix не нашел аудиопотоков.")
            
            print(f"pytubefix скачивает аудиопоток с itag={stream.itag} (лучшее качество)...")
            stream.download(output_path=str(temp_path.parent), filename=temp_path.name)
            print("pytubefix: Аудио успешно скачано.")
        except Exception as e:
            print(f"pytubefix тоже не смог скачать аудио: {e}")
            return None

    # 4. Конвертация в .ogg
    try:
        subprocess.run([
            "ffmpeg", "-i", str(temp_path), "-ac", "1", "-ar", "24000",
            "-c:a", "libopus", "-b:a", "32k", "-y", str(audio_path)
        ], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e_ffmpeg:
        print(f"ffmpeg conversion failed: {e_ffmpeg.stderr}")
        temp_path.unlink(missing_ok=True)
        return None

    # 5. Очистка
    temp_path.unlink(missing_ok=True)
    return audio_path


