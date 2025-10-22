# -*- coding: utf-8 -*- 

import os
import subprocess
import re
import shutil
import json
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

def _get_video_filesize_yt_dlp(url: str) -> int:
    """
    Gets the video filesize using yt-dlp.
    """
    try:
        command = _get_yt_dlp_command(["python3", "-m", "yt_dlp", "-j", url])
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=20)
        video_info = json.loads(result.stdout)
        return video_info.get('filesize') or video_info.get('filesize_approx') or 0
    except Exception as e:
        logger.error(f"An unexpected error occurred while getting video filesize with yt-dlp: {e}")
        return 0

def check_video_availability(url: str, lang: str = 'ru') -> (bool, str, str):
    """
    Checks if a YouTube video is available, has subtitles, and if there is enough disk space.
    First tries pytubefix for availability and subtitles, then falls back to yt-dlp.
    Returns a tuple (is_available, message, error_log).
    """
    # 0. Check for disk space
    filesize = _get_video_filesize_yt_dlp(url)
    if filesize > 0:
        free_space = shutil.disk_usage('.').free
        if filesize > free_space - FREESPACE_LIMIT_MB * 1024 * 1024:
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


def download_video_only(url, video_path):
    try:
        # First, try with pytubefix
        yt = YouTube(url)
        stream = yt.streams.filter(res="1080p", progressive=False, file_extension='mp4').first()
        if not stream:
            stream = yt.streams.filter(type="video", file_extension='mp4').order_by('resolution').desc().first()
        
        if not stream:
            raise ConnectionError("No suitable MP4 video stream found by pytubefix.")

        output_dir = Path(video_path).parent
        file_name = Path(video_path).name
        stream.download(output_path=str(output_dir), filename=file_name)
        print(f"pytubefix: Video downloaded successfully to {video_path}")
        return video_path
    except Exception as e:
        logger.warning(f"pytubefix failed to download video: {e}. Falling back to yt-dlp.")
        # If pytubefix fails, try with yt-dlp
        return _download_video_only_yt_dlp(url, video_path)

def _download_video_only_yt_dlp(url, video_path):
    try:
        command = _get_yt_dlp_command([
            "python3", "-m", "yt_dlp",
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "-o", str(video_path),
            url
        ])
        subprocess.run(command, check=True, timeout=300) # 5-minute timeout
        print(f"yt-dlp: Video downloaded successfully to {video_path}")
        return video_path
    except Exception as e:
        logger.error(f"An error occurred with yt-dlp while downloading video: {e}", exc_info=True)
        raise

def _find_itag_for_lang_with_yt_dlp(url, lang='ru'):
    print(f"Используем yt-dlp для поиска itag для языка '{lang}'...")
    try:
        command = _get_yt_dlp_command(["python3", "-m", "yt_dlp", "-F", url])
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=20)

        lines = result.stdout.split('\n')
        
        audio_streams = []
        table_started = False
        for line in lines:
            if '---' in line: 
                table_started = True
                continue
            if not table_started:
                continue

            if 'audio only' in line and f'[{lang}' in line:
                match = re.match(r'^\s*([\d-]+)\s+', line)
                if match:
                    itag = match.group(1)
                    tbr_match = re.search(r'(\d+)k', line)
                    bitrate = int(tbr_match.group(1)) if tbr_match else 0
                    audio_streams.append({'itag': itag, 'bitrate': bitrate, 'line': line})

        if not audio_streams:
            print("yt-dlp не нашел подходящих аудиопотоков.")
            return None
        
        print("Найденные yt-dlp потоки с указанием языка:")
        for s in audio_streams:
            print(f"- {s['line']}")

        sorted_streams = sorted(audio_streams, key=lambda x: x['bitrate'], reverse=True)
        medium_stream = sorted_streams[len(sorted_streams) // 2]
        print(f"yt-dlp выбрал средний по качеству itag: {medium_stream['itag']}")
        return medium_stream['itag']
        
    except subprocess.TimeoutExpired:
        print("Тайм-аут при вызове yt-dlp для получения форматов.")
        return None
    except Exception as e:
        print(f"Ошибка при вызове yt-dlp для получения форматов: {e}")
        return None

def download_audio_only(url, audio_path, lang='ru'):
    """
    Downloads audio using yt-dlp to find and fetch the correct language,
    falling back to pytubefix if yt-dlp fails.
    """
    audio_path = Path(audio_path).with_suffix(".ogg")
    temp_path = audio_path.with_suffix(".temp.mp4")

    # 1. Find the best itag using yt-dlp's format listing
    itag = _find_itag_for_lang_with_yt_dlp(url, lang)
    
    downloaded = False
    if itag:
        print(f"Попытка скачать аудио с itag={itag} с помощью yt-dlp...")
        try:
            command = _get_yt_dlp_command([
                "python3", "-m", "yt_dlp",
                "-f", itag,  # Use the specific itag
                "--user-agent", "Mozilla/5.0",
                "-o", str(temp_path),
                url
            ])
            subprocess.run(command, check=True)
            print("yt-dlp: Аудио успешно скачано.")
            downloaded = True
        except subprocess.CalledProcessError as e_dlp:
            print(f"yt-dlp не смог скачать аудио с itag={itag}: {e_dlp}")

    # 2. If yt-dlp failed or didn't find an itag, fall back to pytubefix
    if not downloaded:
        print("Переключаемся на pytubefix для скачивания аудио по умолчанию.")
        try:
            yt = YouTube(url)
            streams = yt.streams.filter(only_audio=True).order_by('abr').desc()
            stream = streams[len(streams) // 2] if streams else None
            if not stream:
                raise ConnectionError("pytubefix не нашел аудиопотоков.")
            
            print(f"pytubefix скачивает аудиопоток с itag={stream.itag}...")
            stream.download(output_path=str(temp_path.parent), filename=temp_path.name)
            print(f"pytubefix: Аудио успешно скачано.")
            downloaded = True
        except Exception as e:
            print(f"pytubefix тоже не смог скачать аудио: {e}")
            return None # Both methods failed



    # 2. Convert to the required .ogg format for Whisper
    try:
        subprocess.run([
            "ffmpeg",
            "-i", str(temp_path),
            "-ac", "1",
            "-ar", "24000",
            "-c:a", "libopus",
            "-b:a", "32k",
            "-y",
            str(audio_path)
        ], check=True, capture_output=True, text=True) # Capture output to hide ffmpeg noise unless error
    except subprocess.CalledProcessError as e_ffmpeg:
        print(f"ffmpeg conversion failed: {e_ffmpeg.stderr}")
        return None

    # 3. Clean up the temporary file
    temp_path.unlink(missing_ok=True)

    return audio_path


