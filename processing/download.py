# -*- coding: utf-8 -*- 

import os
import subprocess
import re
from pathlib import Path
from pytubefix import YouTube


import logging

logger = logging.getLogger(__name__)

def check_video_availability(url: str) -> (bool, str, str):
    """
    Checks if a YouTube video is available without downloading it.
    Returns a tuple (is_available, message).
    """
    try:
        yt = YouTube(url)
        # Accessing the title is a lightweight way to check for availability
        _ = yt.title
        # Check if there are any streams available
        if not yt.streams:
            return False, "Видео недоступно, так как для него не найдено ни одного потока для скачивания.", "no streams"
        return True, "Видео доступно.", "Video is available"
    except Exception as e:
        error_message = f"Произошла непредвиденная ошибка при проверке видео: {e}"
        print(error_message)
        if "age restricted" in str(e).lower():
            return False, "⚠️ Обработка не удалась – YouTube пометил этот ролик как 18+, и доступ к исходнику ограничен.\n\nВыбери другой ролик без ограничений — и мы всё сделаем ✨", "age restricted"
        if "private" in str(e).lower():
            return False, "Это видео приватное и не может быть скачано.", "private"
        if "unavailable" in str(e).lower():
            return False, "⚠️ К сожалению, мы не смогли обработать это видео – владелец ролика ограничил его показ по странам и наш сервер не имеет к нему доступа.\nПопробуйте загрузить другое видео — всё должно сработать корректно ✅\n\nСпасибо, что используете Shorts Factory 🙌", str(e)[:100]
        return False, f"Видео недоступно. Пожалуйста, проверьте ссылку или попробуйте другое видео.", str(e)[:100]

def download_video_only(url, video_path):
    """Downloads the best available video up to 720p using pytubefix."""
    try:
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
        logger.error(f"An error occurred with pytubefix while downloading video: {e}", exc_info=True)
        raise

def _find_itag_for_lang_with_yt_dlp(url, lang='ru'):
    print(f"Используем yt-dlp для поиска itag для языка '{lang}'...")
    try:
        command = ["python3", "-m", "yt_dlp", "-F", url]
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

        best_stream = sorted(audio_streams, key=lambda x: x['bitrate'], reverse=True)[0]
        print(f"yt-dlp выбрал лучший itag: {best_stream['itag']}")
        return best_stream['itag']
        
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
            subprocess.run([
                "python3", "-m", "yt_dlp",
                "-f", itag,  # Use the specific itag
                "--user-agent", "Mozilla/5.0",
                "-o", str(temp_path),
                url
            ], check=True)
            print("yt-dlp: Аудио успешно скачано.")
            downloaded = True
        except subprocess.CalledProcessError as e_dlp:
            print(f"yt-dlp не смог скачать аудио с itag={itag}: {e_dlp}")

    # 2. If yt-dlp failed or didn't find an itag, fall back to pytubefix
    if not downloaded:
        print("Переключаемся на pytubefix для скачивания аудио по умолчанию.")
        try:
            yt = YouTube(url)
            stream = yt.streams.get_audio_only()
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


