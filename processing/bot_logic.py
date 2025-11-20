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
from processing.transcription import get_transcript_segments_and_file, get_audio_duration, get_whisper_model
from processing.subtitles import create_ass_subtitles, get_subtitle_items
from config import VIDEO_MAP
from .download import download_video_segment, download_audio_only, get_video_duration
from .layouts import _build_video_canvas
from .gpt import get_highlights_from_gpt, get_random_highlights_from_gpt
from utils import to_seconds, format_seconds_to_hhmmss
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

def transcribe_audio(url: str, out_dir: Path, lang: str):
    """
    Downloads pre-existing subtitles from YouTube.
    If it fails, it raises an exception, and the main workflow will fall back to random clips.
    """
    print("Транскрибируем видео...")
    try:
        transcript_segments, lang_code = get_transcript_segments_and_file(
            url, out_dir=out_dir, force_whisper=False
        )
    except Exception as e:
        print(f"Не удалось получить субтитры с YouTube: {e}")
        raise Exception(get_translation(lang, "transcription_error")) from e

    if not transcript_segments:
        raise ValueError("No transcript segments found.")
        
    # This workflow no longer downloads the full audio, so return None for audio_only
    return transcript_segments, lang_code, None


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

def main(url, config, status_callback=None, send_video_callback=None, deleteOutputAfterSending=False):
    config['bottom_video_path'] = VIDEO_MAP.get(config['bottom_video'])
    lang = config.get('lang', 'ru')
    platform = config.get('platform', 'youtube')

    with temporary_directory(delete=deleteOutputAfterSending) as out_dir:
        if platform == 'twitch':
            return main_twitch(url, config, out_dir, status_callback, send_video_callback)
        
        # YouTube workflow
        if status_callback:
            status_callback(get_translation(lang, "analyzing_video"))
        
        try:
            transcript_segments, _, audio_only = transcribe_audio(url, out_dir, lang)
            if not transcript_segments:
                raise ValueError("Transcription returned no segments.")
        except Exception as e:
            logger.warning(f"Transcription failed: {e}. Falling back to random clips workflow.")
            return handle_random_clips_workflow(url, config, out_dir, status_callback, send_video_callback)

        shorts_number = config.get('shorts_number', 'auto')
        
        try:
            # shorts_timecodes = [{ "start": "02:30:54.1", "end": "02:31:18.1", "hook": ""}]
            shorts_timecodes = get_highlights(out_dir, audio_only, shorts_number)
        except ValueError as e:
            logger.error(f"Не удалось получить хайлайты от GPT: {e}")
            if status_callback:
                status_callback(get_translation(lang, "gpt_highlights_error"))
            return 0, 0
        
        if not shorts_timecodes:
            if status_callback:
                status_callback(get_translation(lang, "gpt_highlights_error"))
            return 0, 0

        num_to_process = len(shorts_timecodes)
        shorts_to_process = shorts_timecodes[:num_to_process]
        extra_found = 0

        if status_callback:
            status_callback(get_translation(lang, "clips_found").format(shorts_timecodes_len=len(shorts_timecodes), num_to_process=num_to_process))
        print(f"Найденные отрезки для шортсов ({len(shorts_timecodes)}):", shorts_timecodes)

        successful_sends = create_clips(config, url, audio_only, shorts_to_process, transcript_segments, out_dir, send_video_callback)

        if audio_only and os.path.exists(audio_only):
            try: os.remove(audio_only)
            except OSError: pass
        
        return successful_sends, extra_found


def handle_random_clips_workflow(url, config, out_dir, status_callback, send_video_callback):
    """
    A workflow for generating clips based on random timestamps.
    Used for Twitch or as a fallback for YouTube.
    """
    lang = config.get('lang', 'ru')
    
    try:
        duration = get_video_duration(url)
        if not duration:
            logger.error(f"yt-dlp did not return a duration for URL {url}, but did not error.")
            return 0, 0
    except Exception as e:
        logger.error(f"FATAL: Failed to get video duration for {url} in fallback workflow. Exception: {e}", exc_info=True)
        return 0, 0
        
    shorts_number = config.get('shorts_number', 'auto')
    try:
        shorts_timecodes_raw = get_random_highlights_from_gpt(shorts_number, duration)
        if not shorts_timecodes_raw:
            raise ValueError("GPT returned no timecodes.")
            
        # Convert seconds to HH:MM:SS format
        shorts_timecodes = []
        for it in shorts_timecodes_raw:
            shorts_timecodes.append({
                "start": format_seconds_to_hhmmss(float(it["start"])),
                "end":   format_seconds_to_hhmmss(float(it["end"])),
                "hook":  it["hook"]
            })

    except Exception as e:
        logger.error(f"Не удалось получить случайные хайлайты от GPT: {e}")
        if status_callback:
            status_callback(get_translation(lang, "gpt_highlights_error"))
        return 0, 0

    if not shorts_timecodes:
        if status_callback:
            status_callback(get_translation(lang, "gpt_highlights_error"))
        return 0, 0

    num_to_process = len(shorts_timecodes)
    if status_callback:
        status_callback(get_translation(lang, "clips_found").format(shorts_timecodes_len=len(shorts_timecodes), num_to_process=num_to_process))

    successful_sends = 0
    futures = []
    for i, short in enumerate(shorts_timecodes, 1):
        try:
            future = create_single_clip(
                config=config,
                url=url,
                short_info=short,
                clip_num=i,
                out_dir=out_dir,
                audio_path=None, 
                full_transcript_segments=None,
                send_video_callback=send_video_callback
            )
            if future:
                futures.append(future)
        except Exception as e:
            logger.error(f"Ошибка при создании клипа #{i}: {e}", exc_info=True)

    for future in futures:
        try:
            if future.result():
                successful_sends += 1
        except Exception as e:
            logger.error(f"Future для отправки видео завершился с ошибкой: {e}")

    return successful_sends, 0


def main_twitch(url, config, out_dir, status_callback, send_video_callback):
    """Entry point for the Twitch workflow."""
    return handle_random_clips_workflow(url, config, out_dir, status_callback, send_video_callback)


def create_single_clip(config, url, short_info, clip_num, out_dir, audio_path, full_transcript_segments, send_video_callback):
    """
    Handles the creation of a single video clip from download to rendering.
    This function is designed to be generic for both YouTube and Twitch.
    """
    final_width = 720
    final_height = 1280
    
    start_cut = to_seconds(short_info["start"])
    end_cut = to_seconds(short_info["end"])
    
    segment_video_path = out_dir / f"segment_{clip_num}.mp4"

    try:
        download_video_segment(url, segment_video_path, start_cut, end_cut)
    except Exception as e:
        print(f"Не удалось скачать сегмент {clip_num} ({start_cut}-{end_cut}): {e}")
        return None

    main_clip_raw = VideoFileClip(str(segment_video_path))
    
    subtitles_type = config.get('subtitles_type', 'word-by-word')
    
    # For Twitch, full_transcript_segments will be None. We generate it per clip.
    # For YouTube, it's passed in.
    current_transcript_segments = full_transcript_segments
    
    # --- Subtitle Generation ---
    ass_path = None
    if subtitles_type != 'no_subtitles':
        faster_whisper_model = get_whisper_model()
        
        # If no transcript is provided (Twitch case), generate it now from the segment
        if current_transcript_segments is None:
            segments, _ = get_transcript_segments_and_file(
                url=None, # Not needed as we provide audio_path
                out_dir=out_dir,
                audio_path=segment_video_path,
                force_whisper=True,
                is_twitch_clip=True
            )
            current_transcript_segments = segments

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
        
        # For word-by-word on YouTube, we need the full audio. For Twitch, we use the segment audio.
        audio_for_subtitles = segment_video_path if audio_path is None else audio_path

        subtitle_items = get_subtitle_items(
            subtitles_type, current_transcript_segments, audio_for_subtitles, start_cut, end_cut, 
            faster_whisper_model)
        
        ass_path = out_dir / f"short{clip_num}.ass"
        
        video_canvas, subtitle_y_pos, subtitle_width = _build_video_canvas(
            config, main_clip_raw, final_width, final_height
        )
        final_clip = video_canvas

        create_ass_subtitles(
            subtitle_items, str(ass_path), final_width, final_height,
            subtitle_y_pos, subtitle_width, config.get('subtitle_style', 'white'), subtitles_type
        )
    else:
        video_canvas, _, _ = _build_video_canvas(config, main_clip_raw, final_width, final_height)
        final_clip = video_canvas

    if config.get('add_banner'):
        banner_path = 'banner.png'
        if os.path.exists(banner_path):
            banner_clip = (ImageClip(banner_path)
                           .set_duration(final_clip.duration)
                           .resize(width=final_clip.w * 0.5)
                           .set_position(('center', final_clip.h * 0.1)))
            final_clip = CompositeVideoClip([final_clip, banner_clip])
        else:
            logger.warning(f"Banner file not found at {banner_path}")

    final_clip = final_clip.set_duration(main_clip_raw.duration)
    output_sub = out_dir / f"short{clip_num}.mp4"

    if ass_path and os.path.exists(ass_path):
        final_clip = final_clip.set_audio(None)
        temp_video_path = out_dir / f"temp_short{clip_num}.mp4"
        final_clip.write_videofile(str(temp_video_path), fps=24, codec="libx264", audio=False)
        
        fonts_dir = "fonts"
        ffmpeg_vf = f"subtitles={str(ass_path)}:fontsdir={fonts_dir}"
        cmd = [
            "ffmpeg", "-i", str(temp_video_path), "-i", str(segment_video_path),
            "-vf", ffmpeg_vf, "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "aac", "-map", "0:v:0", "-map", "1:a:0", "-y", str(output_sub)
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
        final_clip = final_clip.set_audio(main_clip_raw.audio)
        final_clip.write_videofile(str(output_sub), fps=24, codec="libx264", audio_codec="aac")
    
    if os.path.exists(segment_video_path):
        os.remove(segment_video_path)

    print(f"✅ Создан файл {output_sub}")
    
    if send_video_callback:
        return send_video_callback(file_path=output_sub, hook=short_info["hook"], start=short_info["start"], end=short_info["end"])
    return None


def process_video_clips(config, url, audio_path, shorts_timecodes, transcript_segments, out_dir, send_video_callback=None):
    futures = []
    for i, short in enumerate(shorts_timecodes, 1):
        future = create_single_clip(
            config=config,
            url=url,
            short_info=short,
            clip_num=i,
            out_dir=out_dir,
            audio_path=audio_path,
            full_transcript_segments=transcript_segments,
            send_video_callback=send_video_callback
        )
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
