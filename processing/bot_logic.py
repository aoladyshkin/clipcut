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
from .download import download_video_segment, download_audio_only
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

def transcribe_audio(url: str, out_dir: Path, lang: str, force_whisper: bool):
    print("Транскрибируем видео...")
    audio_only = None
    transcript_segments = None
    lang_code = None

    if force_whisper:
        print("Принудительная транскрибация через Whisper.")
        try:
            audio_only = download_audio_only(url, out_dir / "audio_only.ogg")
            if not audio_only:
                raise Exception(get_translation(lang, "download_error"))
            transcript_segments, lang_code = get_transcript_segments_and_file(
                url, out_dir=out_dir, audio_path=audio_only, force_whisper=True
            )
        except Exception as e_whisper:
            raise Exception(get_translation(lang, "transcription_error")) from e_whisper
    else:
        try:
            transcript_segments, lang_code = get_transcript_segments_and_file(
                url, out_dir=out_dir, force_whisper=False
            )
        except Exception as e:
            print(f"Не удалось получить субтитры с YouTube: {e}")
            raise Exception(get_translation(lang, "transcription_error")) from e

    if not transcript_segments:
        print("Не удалось получить транскрипцию.")
        return None, None, None
        
    return transcript_segments, lang_code, audio_only


def get_highlights(out_dir: Path, audio_path: Path, shorts_number: any):
    print("Ищем смысловые куски через GPT...")
    # Используем get_audio_duration только если аудиофайл реально существует
    duration = get_audio_duration(audio_path) if audio_path and audio_path.exists() else None
    
    shorts_timecodes = get_highlights_from_gpt(out_dir / "captions.txt", duration, shorts_number=shorts_number)
    
    if not shorts_timecodes:
        print("GPT не смог выделить подходящие отрезки для шортсов.")
        return None
    
    return shorts_timecodes

def create_clips(config, url, audio_only, shorts_to_process, transcript_segments, out_dir, send_video_callback):
    futures = process_video_clips(config, url, audio_only, shorts_to_process, transcript_segments, out_dir, send_video_callback)
    
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
        if status_callback:
            status_callback(get_translation(lang, "analyzing_video"))
        
        force_ai_transcription = config.get('force_ai_transcription', False)
        
        try:
            transcript_segments, lang_code, audio_only = transcribe_audio(url, out_dir, lang, force_ai_transcription)
        except Exception as e:
            if status_callback:
                status_callback(str(e))
            return 0, 0

        if not transcript_segments:
            if status_callback:
                status_callback(get_translation(lang, "transcription_error"))
            return 0, 0

        shorts_number = config.get('shorts_number', 'auto')
        # shorts_timecodes = [{ "start": "00:00:00.0", "end": "00:01:00.0", "hook": "text"}]
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

        successful_sends = create_clips(config, url, audio_only, shorts_to_process, transcript_segments, out_dir, send_video_callback)

        if audio_only and os.path.exists(audio_only):
            try: os.remove(audio_only)
            except OSError: pass

        return successful_sends, extra_found




def process_video_clips(config, url, audio_path, shorts_timecodes, transcript_segments, out_dir, send_video_callback=None):
    final_width = 720
    final_height = 1280
    futures = []

    # --- Настройки из конфига ---
    subtitles_type = config.get('subtitles_type', 'word-by-word')

    faster_whisper_model = None

    # --- Инициализация faster-whisper (если нужно) ---
    if subtitles_type == 'word-by-word':
        if not audio_path or not audio_path.exists():
            raise ValueError("Для 'word-by-word' субтитров необходим аудиофайл, но он не был скачан.")
        faster_whisper_model = WhisperModel("small", device="cpu", compute_type="int8")

    for i, short in enumerate(shorts_timecodes, 1):
        start_cut = to_seconds(short["start"])
        end_cut = to_seconds(short["end"])
        
        # Путь для скачанного сегмента
        segment_video_path = out_dir / f"segment_{i}.mp4"

        try:
            # Скачиваем только нужный сегмент
            download_video_segment(url, segment_video_path, start_cut, end_cut)
        except Exception as e:
            print(f"Не удалось скачать сегмент {i} ({start_cut}-{end_cut}): {e}")
            continue # Пропускаем этот шортс

        main_clip_raw = VideoFileClip(str(segment_video_path))

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

        video_canvas, subtitle_y_pos, subtitle_width = _build_video_canvas(
            config, main_clip_raw, final_width, final_height
        )

        final_clip = video_canvas
        ass_path = None
        if subtitles_type != 'no_subtitles':
            # --- Создание субтитров ---
            # Для 'word-by-word' нужен аудиофайл. Для остальных - нет.
            if subtitles_type == 'word-by-word' and (not audio_path or not audio_path.exists()):
                 print(f"Пропускаем 'word-by-word' субтитры для клипа {i}, т.к. аудиофайл отсутствует.")
                 ass_path = None
            else:
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

        final_clip = final_clip.set_duration(main_clip_raw.duration)
        
        output_sub = Path(out_dir) / f"short{i}.mp4"

        # Если есть субтитры, прожигаем их
        if ass_path and os.path.exists(ass_path):
            # Устанавливаем аудио из оригинального клипа, т.к. ffmpeg его не копирует при обработке vf
            final_clip = final_clip.set_audio(main_clip_raw.audio)
            
            temp_video_path = out_dir / f"temp_short{i}.mp4"
            final_clip.write_videofile(str(temp_video_path), fps=24, codec="libx264", audio_codec="aac")

            fonts_dir = "fonts"
            ffmpeg_vf = f"subtitles={str(ass_path)}:fontsdir={fonts_dir}"

            cmd = [
                "ffmpeg",
                "-i", str(temp_video_path),
                "-vf", ffmpeg_vf,
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "23",
                "-c:a", "copy",
                "-y", str(output_sub)
            ]
            
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as e:
                print(f"Error burning subtitles with ffmpeg: {e.stderr}")
                shutil.copy(temp_video_path, output_sub)
            finally:
                if os.path.exists(temp_video_path): os.remove(temp_video_path)
                if os.path.exists(ass_path): os.remove(ass_path)
        else:
            # Если субтитров нет, просто сохраняем клип с его собственным аудио
            final_clip = final_clip.set_audio(main_clip_raw.audio)
            final_clip.write_videofile(str(output_sub), fps=24, codec="libx264", audio_codec="aac")
        
        # Очистка скачанного сегмента
        if os.path.exists(segment_video_path):
            os.remove(segment_video_path)

        print(f"✅ Создан файл {output_sub}")
        
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
