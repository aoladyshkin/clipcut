import shutil
import os
import subprocess
import re
import json
import yt_dlp
from pathlib import Path
from pytubefix import YouTube
from config import YOUTUBE_COOKIES_FILE, FREESPACE_LIMIT_MB
from typing import Optional, List, Dict, Tuple, Set
from utils import get_video_platform
from localization import get_translation

import logging

logger = logging.getLogger(__name__)

def get_video_duration(url: str) -> Optional[float]:
    """
    Retrieves the duration of a video in seconds using yt-dlp, with an ffprobe fallback.
    """
    try:
        cleaned_url = url.split('?')[0]
        ydl_opts = {
            'quiet': True,
            'skip_download': True,
            'simulate': True,
            'noplaylist': True,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36',
            }
        }
        if get_video_platform(cleaned_url) == 'youtube' and YOUTUBE_COOKIES_FILE and os.path.exists(YOUTUBE_COOKIES_FILE):
            ydl_opts['cookiefile'] = YOUTUBE_COOKIES_FILE

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(cleaned_url, download=False)
            
            duration = info_dict.get('duration')
            
            # Fallback for Twitch VODs where duration is in the last chapter's end_time
            if duration is None:
                chapters = info_dict.get('chapters')
                if chapters and isinstance(chapters, list):
                    try:
                        last_chapter = chapters[-1]
                        if last_chapter and isinstance(last_chapter, dict):
                            duration = last_chapter.get('end_time')
                            if duration:
                                logger.info(f"Found duration for Twitch VOD in 'chapters' list: {duration}")
                    except (IndexError, TypeError):
                        pass
            
            if duration is None:
                entries = info_dict.get('entries')
                if entries and isinstance(entries, list):
                    try:
                        first_entry = entries[0]
                        if first_entry and isinstance(first_entry, dict):
                            duration = first_entry.get('duration')
                            if duration:
                                logger.info(f"Found duration for Twitch VOD in 'entries' list: {duration}")
                    except (IndexError, TypeError):
                        pass

            # If duration is still not found, try ffprobe
            if duration is None:
                logger.info("yt-dlp failed to find duration, trying ffprobe as a fallback.")
                stream_url = info_dict.get('url') # Get top-level url first
                if not stream_url and 'formats' in info_dict and info_dict['formats']:
                    stream_url = info_dict['formats'][-1].get('url') # Fallback to last format's url

                if stream_url:
                    try:
                        cmd = [
                            "ffprobe",
                            "-v", "error",
                            "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1",
                            stream_url
                        ]
                        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
                        duration_str = result.stdout.strip()
                        if duration_str and duration_str != 'N/A':
                            duration = float(duration_str)
                            logger.info(f"ffprobe successfully found duration: {duration}")
                    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
                        logger.error(f"ffprobe failed to get duration: {e}")
                    except Exception as e:
                        logger.error(f"An unexpected error occurred with ffprobe: {e}")

            if duration is None:
                logger.warning(f"Could not find 'duration' in any known location for {cleaned_url}.")

            return duration
    except Exception as e:
        logger.error(f"Exception in get_video_duration for {url} with yt-dlp: {e}")
        raise e

def check_video_availability(url: str, lang: str = 'ru') -> (bool, str, str):
    """
    Checks if a video is available and if there is enough disk space.
    For YouTube, it also checks for subtitles.
    Returns a tuple (is_available, message, error_log).
    """
    # 0. Check for disk space
    free_space_mb = shutil.disk_usage('.').free / (1024 * 1024)
    if free_space_mb < FREESPACE_LIMIT_MB:
        return False, get_translation(lang, "not_enough_disk_space"), "not enough disk space"

    platform = get_video_platform(url)

    # 1. Check for video availability
    if platform == 'youtube':
        info_dict, message, err = _get_video_info_yt_dlp(url, lang)
        
        if not info_dict:
            return False, message, err
    
    elif platform == 'twitch':
        try:
            cleaned_url = url.split('?')[0]
            ydl_opts = {
                'quiet': True,
                'skip_download': True,
                'simulate': True,
                'noplaylist': True,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36',
                }
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(cleaned_url, download=False)

            title = info_dict.get('title')
            if not title:
                entries = info_dict.get('entries')
                if entries and isinstance(entries, list) and isinstance(entries[0], dict):
                    title = entries[0].get('title')
            
            if not title:
                return False, get_translation(lang, "unavailable_video_error"), "yt-dlp found no title"

        except yt_dlp.utils.DownloadError as e:
            error_message = str(e).lower()
            if "age restricted" in error_message:
                return False, get_translation(lang, "age_restricted_error"), "age restricted"
            if "private" in error_message:
                return False, get_translation(lang, "private_video_error"), "private"
            if "unavailable" in error_message:
                return False, get_translation(lang, "unavailable_video_error"), error_message[:400]
            return False, get_translation(lang, "video_unavailable_check_link"), error_message
        except Exception as e:
            logger.error(f"Error checking twitch video availability {url}: {e}")
            return False, get_translation(lang, "video_unavailable_check_link"), str(e)

    else:
        return False, "Unsupported video platform", "unsupported_platform"

    return True, get_translation(lang, "video_available"), "Video is available"

def _get_video_info_yt_dlp(url: str, lang: str = 'ru') -> (Optional[dict], str, str):
    """
    Retrieves video info using the yt-dlp API.
    Returns (info_dict, message, error_log)
    """
    try:
        cleaned_url = url.split('?')[0]
        ydl_opts = {
            'quiet': True,
            'skip_download': True,
            'simulate': True,
            'listsubtitles': True,
            'noplaylist': True,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36',
            }
        }
        if get_video_platform(cleaned_url) == 'youtube' and YOUTUBE_COOKIES_FILE and os.path.exists(YOUTUBE_COOKIES_FILE):
            ydl_opts['cookiefile'] = YOUTUBE_COOKIES_FILE

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(cleaned_url, download=False)
            if info_dict.get('title'):
                return info_dict, get_translation(lang, "video_available"), "Video is available"
            else:
                return None, get_translation(lang, "unavailable_video_error"), "yt-dlp found no title"
    except yt_dlp.utils.DownloadError as e:
        error_message = str(e).lower()
        if "age restricted" in error_message:
            return None, get_translation(lang, "age_restricted_error"), "age restricted"
        if "private" in error_message:
            return None, get_translation(lang, "private_video_error"), "private"
        if "unavailable" in error_message:
            return None, get_translation(lang, "unavailable_video_error"), error_message[:400]
        return None, get_translation(lang, "video_unavailable_check_link"), error_message
    except Exception as e:
        logger.error(f"An unexpected error occurred while checking video info with yt-dlp API: {e}")
        return None, get_translation(lang, "video_unavailable_check_link"), str(e)




from typing import List, Dict, Tuple, Optional, Set

def _get_available_audio_langs(url: str, yt_dlp_command: list) -> Set[str]:
    """Использует yt-dlp для получения списка всех доступных языков аудио."""
    try:
        command = list(yt_dlp_command) # Create a copy
        command.extend(["-F", url])
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
        command = list(yt_dlp_command) # Create a copy
        command.extend(["-F", url])
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

    def range_func(info_dict, ydl):
        return [{'start_time': start_time, 'end_time': end_time}]

    ydl_opts = {
        'format': 'best[height<=1080][ext=mp4]/best[ext=mp4]',
        'outtmpl': output_path,
        'noplaylist': True,
        'download_ranges': range_func,
        'force_keyframes_at_cuts': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36',
        },
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web']
            }
        },
        'downloader_args': {
            'ffmpeg': [
                '-c:v', 'libx264',
                '-preset', 'medium',
                '-crf', '20',
                '-c:a', 'aac',
                '-b:a', '192k'
            ]
        }
    }

    if get_video_platform(url) == 'youtube' and YOUTUBE_COOKIES_FILE and os.path.exists(YOUTUBE_COOKIES_FILE):
        ydl_opts['cookiefile'] = YOUTUBE_COOKIES_FILE

    try:
        print(f"Downloading segment from {start_time} to {end_time} using yt-dlp download_ranges...")
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
    
    base_yt_dlp_command = ["python3", "-m", "yt_dlp", "--no-playlist"]
    base_yt_dlp_command.extend(_get_cookie_args(url))


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
            command = list(base_yt_dlp_command)
            command.extend(["-f", itag, "--user-agent", "Mozilla/5.0", "-o", str(temp_path), url])
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
