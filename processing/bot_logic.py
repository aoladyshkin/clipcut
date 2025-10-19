# -*- coding: utf-8 -*- 

import os
import shutil

from pathlib import Path
import logging
import contextlib


import subprocess
from moviepy.editor import (
    VideoFileClip,
    CompositeVideoClip, ImageClip
)
import json
from faster_whisper import WhisperModel
from processing.transcription import get_transcript_segments_and_file, get_audio_duration
from processing.subtitles import create_ass_subtitles, get_subtitle_items
from config import VIDEO_MAP
from .download import download_video_only, download_audio_only
from .layouts import _build_video_canvas
from .gpt import get_highlights_from_gpt
from utils import to_seconds
from localization import get_translation



logger = logging.getLogger(__name__)



def get_unique_output_dir(base="output"):
    n = 1
    while True:
        out_dir = f"{base}{n}"
        if not Path(out_dir).exists():
            Path(out_dir).mkdir(parents=True)
            return out_dir
        n += 1

@contextlib.contextmanager
def temporary_directory(delete: bool = True):
    """Context manager for creating and cleaning up a temporary directory."""
    temp_dir = get_unique_output_dir()
    try:
        yield Path(temp_dir)
    finally:
        if delete:
            shutil.rmtree(temp_dir)
            print(f"🗑️ Папка {temp_dir} удалена.")

def download_media(url: str, out_dir: Path, audio_lang: str, lang: str):
    print("Скачиваем видео с YouTube...")
    try:
        video_only = download_video_only(url, out_dir / "video_only.mp4")
        audio_only = download_audio_only(url, out_dir / "audio_only.ogg", lang=audio_lang)
    except Exception as e:
        raise Exception(get_translation(lang, "download_error")) from e

    if not video_only or not audio_only:
        raise Exception(get_translation(lang, "download_error"))
    
    video_full = merge_video_audio(video_only, audio_only, out_dir / "video.mp4")
    # video_full = out_dir / Path('video.mp4')
    # audio_only = out_dir / Path('audio_only.ogg')
    return video_full, audio_only

def transcribe_audio(url: str, out_dir: Path, audio_path: Path, force_whisper: bool):
    print("Транскрибируем видео...")
    transcript_segments, lang_code = get_transcript_segments_and_file(url, out_dir=out_dir, audio_path=audio_path, force_whisper=force_whisper)

    if not transcript_segments:
        print("Не удалось получить транскрипцию.")
        return None, None
    return transcript_segments, lang_code

def get_highlights(out_dir: Path, audio_only: Path, shorts_number: any):
    print("Ищем смысловые куски через GPT...")
    shorts_timecodes = get_highlights_from_gpt(out_dir / "captions.txt", get_audio_duration(audio_only), shorts_number=shorts_number)
    
    if not shorts_timecodes:
        print("GPT не смог выделить подходящие отрезки для шортсов.")
        return None
    
    return shorts_timecodes

def create_clips(config, video_full, audio_only, shorts_to_process, transcript_segments, out_dir, send_video_callback):
    futures = process_video_clips(config, video_full, audio_only, shorts_to_process, transcript_segments, out_dir, send_video_callback)
    
    successful_sends = 0
    if futures:
        for future in futures:
            try:
                success = future.result() # this will block
                if success:
                    successful_sends += 1
            except Exception as e:
                print(f"A future failed when sending video: {e}")
    return successful_sends

def main(url, config, status_callback=None, send_video_callback=None, deleteOutputAfterSending=False, user_balance: int = None):

    config['bottom_video_path'] = VIDEO_MAP.get(config['bottom_video'])
    lang = config.get('lang', 'ru')

    with temporary_directory(delete=deleteOutputAfterSending) as out_dir:
        # out_dir = Path('output6')
        audio_lang = config.get('audio_lang', 'ru')
        video_full, audio_only = download_media(url, out_dir, audio_lang, lang)

        if status_callback:
            status_callback(get_translation(lang, "analyzing_video"))
        
        force_ai_transcription = config.get('force_ai_transcription', False)
        # transcript_segments = []
        transcript_segments, lang_code = transcribe_audio(url, out_dir, audio_only, force_ai_transcription)
        
        if not transcript_segments:
            return 0, 0
        
        shorts_number = config.get('shorts_number', 'auto')
        # shorts_timecodes = [{ "start": "01:12:28.0", "end": "01:12:55.0", "hook": '' }]
        shorts_timecodes = get_highlights(out_dir, audio_only, shorts_number)
        
        if not shorts_timecodes:
            if status_callback:
                status_callback(get_translation(lang, "gpt_highlights_error"))
            return 0, 0

        if user_balance is None:
            user_balance = len(shorts_timecodes)

        num_to_process = min(len(shorts_timecodes), user_balance)
        shorts_to_process = shorts_timecodes[:num_to_process]
        extra_found = len(shorts_timecodes) - num_to_process

        if status_callback:
            status_callback(get_translation(lang, "clips_found").format(shorts_timecodes_len=len(shorts_timecodes), num_to_process=num_to_process))
        print(f"Найденные отрезки для шортсов ({len(shorts_timecodes)}):", shorts_timecodes)

        successful_sends = create_clips(config, video_full, audio_only, shorts_to_process, transcript_segments, out_dir, send_video_callback)

        if os.path.exists(audio_only):
            try: os.remove(audio_only)
            except OSError: pass

        return successful_sends, extra_found


def merge_video_audio(video_path, audio_path, output_path):

    video_path = str(video_path)
    audio_path = str(audio_path)
    output_path = str(output_path)

    # ffmpeg команда: конвертируем аудио в AAC для совместимости с MP4
    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",        # копируем видео без перекодирования
        "-c:a", "aac",         # конвертируем аудио в AAC
        "-b:a", "128k",        # битрейт аудио
        "-shortest",           # чтобы длительность файла была равна меньшей из видео/аудио
        "-y",                  # перезаписываем если есть
        output_path
    ]

    subprocess.run(cmd, check=True)
    
    # удаляем видео без звука
    if os.path.exists(video_path):
        os.remove(video_path)
        
    return output_path

def process_video_clips(config, video_path, audio_path, shorts_timecodes, transcript_segments, out_dir, send_video_callback=None):
    final_width = 720
    final_height = 1280
    futures = []

    # --- Настройки из конфига ---
    subtitles_type = config.get('subtitles_type', 'word-by-word')

    faster_whisper_model = None

    # --- Инициализация faster-whisper (если нужно) ---
    if subtitles_type == 'word-by-word':
        faster_whisper_model = WhisperModel("small", device="cpu", compute_type="int8")

    # No longer collecting results in a list to return
    for i, short in enumerate(shorts_timecodes, 1):
        output_sub = Path(out_dir) / f"short{i}.mp4"
        start_cut = to_seconds(short["start"])
        end_cut = to_seconds(short["end"])

        current_transcript_segments = json.loads(json.dumps(transcript_segments))

        if not config.get('capitalize_sentences', True):
            for seg in current_transcript_segments:
                if seg['start'] >= start_cut:
                    text = seg['text']
                    lstripped_text = text.lstrip()
                    if lstripped_text:
                        new_text = lstripped_text[0].lower() + lstripped_text[1:]
                        seg['text'] = new_text
                if seg['start'] > end_cut:
                    break

        main_clip_raw = VideoFileClip(str(video_path)).subclip(start_cut, end_cut)

        video_canvas, subtitle_y_pos, subtitle_width = _build_video_canvas(
            config, main_clip_raw, final_width, final_height
        )

        final_clip = video_canvas
        ass_path = None
        if subtitles_type != 'no_subtitles':
            # --- Создание субтитров ---
            subtitle_items = get_subtitle_items(
                subtitles_type, current_transcript_segments, audio_path, start_cut, end_cut, 
                faster_whisper_model)
            
            ass_path = out_dir / f"short{i}.ass"
            create_ass_subtitles(
                subtitle_items, 
                str(ass_path),
                final_width,
                final_height,
                subtitle_y_pos, 
                subtitle_width, 
                config.get('subtitle_style', 'white'),
                subtitles_type
            )

        if config.get('add_banner'):
            banner_path = 'banner.png'
            if os.path.exists(banner_path):
                banner_clip = (ImageClip(banner_path)
                               .set_duration(final_clip.duration)
                               .resize(width=final_clip.w * 0.5)
                               .set_position(('left', 'top')))
                final_clip = CompositeVideoClip([final_clip, banner_clip])
            else:
                logger.warning(f"Banner file not found at {banner_path}")

        final_clip = final_clip.set_duration(video_canvas.duration)

        # If there are subtitles, we need to burn them using ffmpeg directly
        if ass_path and os.path.exists(ass_path):
            temp_video_path = out_dir / f"temp_short{i}.mp4"
            final_clip.write_videofile(str(temp_video_path), fps=24, codec="libx264", audio_codec="aac")

            fonts_dir = "fonts" # Relative path to fonts dir
            
            ffmpeg_vf = f"subtitles={str(ass_path)}:fontsdir={fonts_dir}"

            cmd = [
                "ffmpeg",
                "-i", str(temp_video_path),
                "-vf", ffmpeg_vf,
                "-c:v", "libx264", # re-encoding is necessary
                "-preset", "medium",
                "-crf", "23",
                "-c:a", "copy",
                "-y", str(output_sub)
            ]
            
            try:
                # Using subprocess.run to wait for completion
                result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as e:
                print(f"Error burning subtitles with ffmpeg: {e.stderr}")
                # As a fallback, just copy the non-subtitled video
                shutil.copy(temp_video_path, output_sub)
            finally:
                # Clean up temporary files
                if os.path.exists(temp_video_path):
                    os.remove(temp_video_path)
                if os.path.exists(ass_path):
                    os.remove(ass_path)
        else:
            # No subtitles, just write the file
            final_clip.write_videofile(str(output_sub), fps=24, codec="libx264", audio_codec="aac")
        
        print(f"✅ Создан файл {output_sub}")
        
        # Call the callback to send the video
        if send_video_callback:
            future = send_video_callback(file_path=output_sub, hook=short["hook"], start=short["start"], end=short["end"])
            if future:
                futures.append(future)
    return futures






if __name__ == "__main__":
    url = "https://youtu.be/4_3VXLK_K_A?si=GVZ3IySlOPK09Ohc"
    # ================== КОНФИГУРАЦИЯ ==================
    config = {
        # Опции: 'white', 'yellow'
        'subtitle_style': 'yellow',
        
        # Опции: 'gta', 'minecraft' или None для черного фона
        'bottom_video': 'minecraft', 
        
        # Опции: 'square_top_brainrot_bottom', 'square_center', 'full_top_brainrot_bottom', 'full_center', 'face_track_9_16'
        'layout': 'square_center',

        # Опции: 'word-by-word', 'phrases', None
        'subtitles_type': None,

        # Опции: True, False
        'capitalize_sentences': True
    }
    # ================================================
    shorts = main(url, config)
