# -*- coding: utf-8 -*- 

import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue


from pathlib import Path
import logging
import contextlib


import subprocess
from moviepy.editor import (
    VideoFileClip,
    CompositeVideoClip, ImageClip,
    vfx, concatenate_videoclips
)
import json
from faster_whisper import WhisperModel
from processing.transcription import get_transcript_segments_and_file, get_audio_duration
from processing.subtitles import create_ass_subtitles, get_subtitle_items
from config import VIDEO_MAP, MAX_SHORTS_PER_VIDEO, MIN_SHORT_DURATION, MAX_SHORT_DURATION
from .download import download_video_segment, get_video_duration, get_video_heatmap, download_audio_track
from .layouts import _build_video_canvas
from .gpt import get_highlights_from_gpt, get_random_highlights
from utils import to_seconds, format_seconds_to_hhmmss, get_video_platform
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
    Returns None if transcription fails.
    """
    print("Транскрибируем видео...")
    platform = get_video_platform(url)
    audio_path = None

    try:
        if platform == 'general' or platform == 'google_drive':
            audio_path = out_dir / "audio.mp3"
            download_audio_track(url, str(audio_path))
            transcript_segments, lang_code = get_transcript_segments_and_file(
                url=None, out_dir=out_dir, audio_path=audio_path, force_whisper=True
            )
        else:
            transcript_segments, lang_code = get_transcript_segments_and_file(
                url, out_dir=out_dir, force_whisper=False
            )

        if not transcript_segments:
            raise ValueError("No transcript segments found.")
        return transcript_segments, lang_code, audio_path
    except Exception as e:
        logger.warning(f"Не удалось получить субтитры (пропускаем): {e}")
        return None, None, None


def _refine_heatmap_segment(start, end, heatmap, min_dur):
    """
    Refines a segment by finding the sub-segment with the highest heatmap density.
    """
    duration = end - start
    if duration <= min_dur:
        # Calculate density for the whole segment
        total_val = 0.0
        for point in heatmap:
             p_start = point.get('start_time', 0)
             p_end = point.get('end_time', 0)
             val = point.get('value', 0)
             overlap = max(0, min(end, p_end) - max(start, p_start))
             total_val += overlap * val
        return start, end, (total_val / duration if duration > 0 else 0)
    
    resolution = 1.0
    num_bins = int(duration / resolution)
    bins = [0.0] * num_bins
    
    for point in heatmap:
        p_start = point.get('start_time', 0)
        p_end = point.get('end_time', 0)
        val = point.get('value', 0)
        
        s_inter = max(start, p_start)
        e_inter = min(end, p_end)
        
        if s_inter < e_inter:
            b_s = int((s_inter - start) / resolution)
            b_e = int((e_inter - start) / resolution)
            for i in range(b_s, min(b_e + 1, num_bins)):
                bin_start = start + i * resolution
                bin_end = start + (i + 1) * resolution
                ov_s = max(bin_start, s_inter)
                ov_e = min(bin_end, e_inter)
                if ov_s < ov_e:
                    bins[i] += (ov_e - ov_s) * val

    best_s = start
    best_e = end
    max_density = -1.0
    
    for i in range(num_bins):
        current_sum = 0.0
        for j in range(i, num_bins):
            current_sum += bins[j]
            current_dur = (j - i + 1) * resolution
            
            if current_dur >= min_dur:
                density = current_sum / current_dur
                if density > max_density:
                    max_density = density
                    best_s = start + i * resolution
                    best_e = start + (j + 1) * resolution
                    
    return best_s, best_e, max_density

def get_highlights(url: str, out_dir: Path, audio_path: Path, shorts_number: any, video_duration: float):
    print("Ищем виральные моменты...")
    # Используем get_audio_duration только если аудиофайл реально существует
    duration = video_duration if video_duration else (get_audio_duration(audio_path) if audio_path and audio_path.exists() else 0)
    
    shorts_timecodes = []

    # 1. Heatmap Strategy
    try:
        print("Попытка получить Heatmap...")
        heatmap = get_video_heatmap(url)
        print(heatmap)
        if heatmap:
            count = 3
            if shorts_number == 'auto':
                if duration < 600: count = 3
                elif duration < 1200: count = 5
                elif duration < 2400: count = 8
                else: count = 10
            else:
                try:
                    count = int(shorts_number)
                except:
                    count = 3
            
            count = min(count, MAX_SHORTS_PER_VIDEO)
            
            window_size = float(MAX_SHORT_DURATION)
            step = 5.0
            candidates = []
            curr = 0.0
            
            while curr + window_size <= duration:
                w_start = curr
                w_end = curr + window_size
                score = 0.0
                for point in heatmap:
                    p_start = point.get('start_time', 0)
                    p_end = point.get('end_time', 0)
                    val = point.get('value', 0)
                    overlap = max(0, min(w_end, p_end) - max(w_start, p_start))
                    score += overlap * val
                candidates.append({'start': w_start, 'end': w_end, 'score': score})
                curr += step
            
            candidates.sort(key=lambda x: x['score'], reverse=True)
            selected = []
            for cand in candidates:
                if len(selected) >= count: break
                if not any(not (cand['end'] <= s['start'] or cand['start'] >= s['end']) for s in selected):
                    r_start, r_end, r_density = _refine_heatmap_segment(cand['start'], cand['end'], heatmap, float(MIN_SHORT_DURATION))
                    selected.append({'start': r_start, 'end': r_end, 'score': cand['score'], 'density': r_density})
            
            if selected:
                for s in selected:
                    avg_val = s['density']
                    v_score = max(1, min(10, int(round(avg_val * 10)*2)))  # Scale to 1-10 and boost by 1.5x
                    shorts_timecodes.append({
                        "start": format_seconds_to_hhmmss(s['start']),
                        "end": format_seconds_to_hhmmss(s['end']),
                        "hook": "",
                        "virality_score": v_score
                    })
                print(f"Heatmap вернул {len(shorts_timecodes)} отрезков.")
                return shorts_timecodes
    except Exception as e:
        logger.warning(f"Heatmap processing failed: {e}")

    # 2. GPT Strategy
    print("Ищем смысловые куски через GPT...")
    captions_file = out_dir / "captions.txt"
    try:
        shorts_timecodes = get_highlights_from_gpt(out_dir / "captions.txt", duration, shorts_number=shorts_number)
        if not captions_file.exists():
            raise FileNotFoundError("Файл субтитров не найден, пропускаем GPT.")
            
        shorts_timecodes = get_highlights_from_gpt(captions_file, duration, shorts_number=shorts_number)
        if not shorts_timecodes:
            # Вызываем ошибку, чтобы перейти в блок except и использовать fallback
            raise ValueError("GPT не вернул таймкоды")
        return shorts_timecodes
    except Exception as e:
        logger.warning("Не удалось получить хайлайты от GPT (%s), переход к случайной генерации.", e)
        logger.warning(f"Не удалось получить хайлайты от GPT ({e}), переход к случайной генерации.")
        
        # 3. Random Fallback
        try:
            shorts_timecodes_raw = get_random_highlights(shorts_number, duration)
            if not shorts_timecodes_raw:
                print("Не удалось сгенерировать случайные отрезки.")
                return None
            
            # Конвертируем секунды в формат HH:MM:SS
            for it in shorts_timecodes_raw:
                shorts_timecodes.append({
                    "start": format_seconds_to_hhmmss(float(it["start"])),
                    "end":   format_seconds_to_hhmmss(float(it["end"])),
                    "hook":  it["hook"],
                    "virality_score": it.get("virality_score", 5)
                })
            return shorts_timecodes
        except Exception as fallback_e:
            print(f"Ошибка при генерации случайных отрезков: {fallback_e}")
            logger.error("Не удалось сгенерировать случайные отрезки в качестве фолбэка: %s", fallback_e)
            return None

    return None

def create_clips(config, url, audio_only, shorts_to_process, transcript_segments, out_dir, send_video_callback):
    render_futures = process_video_clips(config, url, audio_only, shorts_to_process, transcript_segments, out_dir, send_video_callback)
    
    successful_sends = 0
    if render_futures:
        for render_future in render_futures:
            try:
                # Первый .result() ожидает завершения задачи _render_clip_from_segment.
                # Он возвращает future, созданный run_coroutine_threadsafe.
                send_future = render_future.result()
                
                if send_future:
                    # Второй .result() ожидает завершения корутины send_video.
                    # Это блокирующий вызов, который исправляет состояние гонки.
                    # Добавлен таймаут, чтобы избежать вечного зависания.
                    success = send_future.result(timeout=600) 
                    if success:
                        successful_sends += 1
            except Exception as e:
                logger.error("Future для отправки видео завершился с ошибкой: %s", e, exc_info=True)

    return successful_sends

def main(url, config, status_callback=None, send_video_callback=None, deleteOutputAfterSending=False):
    config['bottom_video_path'] = VIDEO_MAP.get(config['bottom_video'])
    lang = config.get('lang', 'ru')
    platform = config.get('platform', 'youtube')
    shorts_number = config.get('shorts_number', 'auto')

    with temporary_directory(delete=deleteOutputAfterSending) as out_dir:
        if platform == 'twitch':
            return main_twitch(url, config, out_dir, status_callback, send_video_callback)
        
        # YouTube workflow
        if status_callback:
            status_callback(get_translation(lang, "analyzing_video"))
        
        # 1. Получаем длительность (Критично для всего)
        video_duration = get_video_duration(url)
        if not video_duration:
            logger.error(f"Failed to get video duration for {url}")
            return 0, 0

        # 2. Пробуем транскрибировать (Опционально: нужно для GPT и субтитров)
        # Если не получится - вернет None, и мы просто не будем использовать GPT/субтитры
        transcript_segments, _, audio_only = transcribe_audio(url, out_dir, lang)

        # 3. Определяем хайлайты (Heatmap -> GPT -> Random)
        shorts_timecodes = get_highlights(url, out_dir, audio_only, shorts_number, video_duration)
        
        if not shorts_timecodes:
            logger.error("Не удалось получить таймкоды ни одним из методов.")
            if status_callback:
                status_callback(get_translation(lang, "gpt_highlights_error"))
            return 0, 0
            
        shorts_timecodes.sort(key=lambda x: x.get('virality_score', 0), reverse=True)

        num_to_process = len(shorts_timecodes)
        shorts_to_process = shorts_timecodes[:num_to_process]
        extra_found = 0

        if status_callback:
            status_callback(get_translation(lang, "clips_found").format(shorts_timecodes_len=len(shorts_timecodes), num_to_process=num_to_process))
        print(f"Найденные отрезки для шортсов ({len(shorts_timecodes)}):", shorts_timecodes)

        # 4. Создаем клипы
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
        shorts_timecodes_raw = get_random_highlights(shorts_number, duration)
        if not shorts_timecodes_raw:
            raise ValueError("GPT returned no timecodes.")
            
        # Convert seconds to HH:MM:SS format
        shorts_timecodes = []
        for it in shorts_timecodes_raw:
            shorts_timecodes.append({
                "start": format_seconds_to_hhmmss(float(it["start"])),
                "end":   format_seconds_to_hhmmss(float(it["end"])),
                "hook":  it["hook"],
                "virality_score": it.get("virality_score", 5)
            })

        # Sort by virality score
        shorts_timecodes.sort(key=lambda x: x.get('virality_score', 0), reverse=True)

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

    # The new orchestrator function handles the rest
    futures = orchestrate_clip_creation(
        config=config,
        url=url,
        shorts_timecodes=shorts_timecodes,
        out_dir=out_dir,
        send_video_callback=send_video_callback,
        audio_path=None,
        full_transcript_segments=None,
        status_callback=status_callback
    )

    successful_sends = 0
    for render_future in futures:
        try:
            # .result() on the render_future waits for _render_clip_from_segment to complete
            # and returns the send_future created by run_coroutine_threadsafe
            send_future = render_future.result()
            
            if send_future:
                # .result() on the send_future waits for the send_video coroutine to finish
                success = send_future.result(timeout=600) 
                if success:
                    successful_sends += 1
        except Exception as e:
            logger.error(f"Future для отправки видео завершился с ошибкой: {e}", exc_info=True)

    return successful_sends, 0

def main_twitch(url, config, out_dir, status_callback, send_video_callback):
    """Entry point for the Twitch workflow."""
    return handle_random_clips_workflow(url, config, out_dir, status_callback, send_video_callback)


def _render_clip_from_segment(config, segment_video_path, short_info, clip_num, out_dir, audio_path, full_transcript_segments, send_video_callback):
    """
    Handles the rendering of a single video clip from an already downloaded segment.
    """
    final_width = 720
    final_height = 1280
    start_cut = to_seconds(short_info["start"])
    end_cut = to_seconds(short_info["end"])

    main_clip_raw = VideoFileClip(str(segment_video_path))
    
    subtitles_type = config.get('subtitles_type', 'word-by-word')
    
    current_transcript_segments = full_transcript_segments
    
    ass_path = None
    if subtitles_type != 'no_subtitles':
        if current_transcript_segments is None:
            segments, _ = get_transcript_segments_and_file(
                url=None, 
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
        
        audio_for_subtitles = segment_video_path if audio_path is None else audio_path
        subtitle_items = get_subtitle_items(
            subtitles_type, current_transcript_segments, audio_for_subtitles, start_cut, end_cut)
        
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
        banner_type = config.get('add_banner')
        if banner_type == 'shorts_factory_banner':
            banner_path = 'banner.png'
            if os.path.exists(banner_path):
                banner_clip = (ImageClip(banner_path)
                               .set_duration(final_clip.duration)
                               .resize(width=final_clip.w * 0.4)
                               .set_position(('center', final_clip.h * 0.1)))
                final_clip = CompositeVideoClip([final_clip, banner_clip])
            else:
                logger.warning(f"Banner file not found at {banner_path}")
        elif banner_type == 'getcourse_banner':
            banner_path = 'getcourse_banner_encoded.mp4'
            if os.path.exists(banner_path):
                banner_video = VideoFileClip(banner_path).without_audio()
                banner_video = banner_video.loop(duration=final_clip.duration)
                banner_clip = (banner_video
                               .resize(width=final_clip.w * 0.5) # Half width
                               .set_position(('center', final_clip.h * 0.1))) # Centered horizontally, 10% from top
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
        virality_score = short_info.get("virality_score", None) # Get score, default to None
        return send_video_callback(file_path=output_sub, hook=short_info["hook"], start=short_info["start"], end=short_info["end"], virality_score=virality_score)
    return None

def orchestrate_clip_creation(config, url, shorts_timecodes, out_dir, send_video_callback, audio_path=None, full_transcript_segments=None, status_callback=None):
    """
    Orchestrates the creation of video clips using a producer-consumer pattern.
    Downloads segments sequentially while rendering them sequentially, but overlapping the two phases.
    """
    render_futures = []       # Futures for the rendering tasks
    
    # 1. Define the downloader worker function
    def _download_worker_task(clip_num, short_info):
        start_cut = to_seconds(short_info["start"])
        end_cut = to_seconds(short_info["end"])
        segment_video_path = out_dir / f"segment_{clip_num}.mp4"
        try:
            print(f"Downloading segment {clip_num} ({short_info['start']}-{short_info['end']})...")
            download_video_segment(url, segment_video_path, start_cut, end_cut)
            return clip_num, segment_video_path, short_info
        except Exception as e:
            logger.error(f"Failed to download segment {clip_num} ({start_cut}-{end_cut}): {e}", exc_info=True)
            return clip_num, None, short_info

    # 2. Start the downloader in a single-worker executor
    download_executor = ThreadPoolExecutor(max_workers=1)
    # Submit all download tasks to the downloader executor.
    # They will be executed sequentially by this executor.
    download_submission_futures = [
        download_executor.submit(_download_worker_task, i + 1, short)
        for i, short in enumerate(shorts_timecodes)
    ]

    # 3. Start the renderer in a single-worker executor
    render_executor = ThreadPoolExecutor(max_workers=1)

    download_count = 0
    total_downloads = len(shorts_timecodes)

    # 4. As downloads complete, feed them to the renderer
    for future in as_completed(download_submission_futures):
        clip_num, segment_path, short_info = future.result()
        if segment_path:
            download_count += 1
            print(f"Finished downloading segment {clip_num}. {download_count}/{total_downloads} downloaded.")
            # if status_callback:
            #     lang = config.get('lang', 'ru')
            #     status_callback(get_translation(lang, "downloading_clips").format(current=download_count, total=total_downloads))
            
            # Submit rendering task for this downloaded segment
            print(f"Submitting clip #{clip_num} for rendering...")
            render_future = render_executor.submit(
                _render_clip_from_segment,
                config=config,
                segment_video_path=segment_path,
                short_info=short_info,
                clip_num=clip_num,
                out_dir=out_dir,
                audio_path=audio_path,
                full_transcript_segments=full_transcript_segments,
                send_video_callback=send_video_callback
            )
            render_futures.append(render_future)
        else:
            print(f"Skipping rendering for clip #{clip_num} due to failed download.")
            logger.warning(f"Clip #{clip_num} download failed, skipping rendering.")

    # Shut down executors
    download_executor.shutdown(wait=True)
    render_executor.shutdown(wait=True)
    
    # Return futures for the send_video_callback results
    return render_futures


def process_video_clips(config, url, audio_path, shorts_timecodes, transcript_segments, out_dir, send_video_callback=None):
    return orchestrate_clip_creation(
        config=config,
        url=url,
        shorts_timecodes=shorts_timecodes,
        out_dir=out_dir,
        send_video_callback=send_video_callback,
        audio_path=audio_path,
        full_transcript_segments=transcript_segments
    )

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
