# -*- coding: utf-8 -*- 

import os
import shutil
from dotenv import load_dotenv
from pathlib import Path
import logging

load_dotenv()
import subprocess
import random
from moviepy.editor import (
    VideoFileClip,
    TextClip,
    CompositeVideoClip,
    ColorClip,
    clips_array,
    vfx,
    concatenate_videoclips,
)
import json
from openai import OpenAI
import time
import tempfile
import re
import cv2
import numpy as np
from faster_whisper import WhisperModel
from processing.transcription import get_transcript_segments_and_file, get_audio_duration
from processing.subtitles import create_subtitle_clips, get_subtitle_items


client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

from pytubefix import YouTube

logger = logging.getLogger(__name__)


def format_seconds_to_hhmmss(seconds):
    seconds = float(seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:04.1f}"

def to_seconds(t: str) -> float:
    h, m, s_part = t.split(':')
    s = float(s_part)
    return int(h) * 3600 + int(m) * 60 + s

def get_unique_output_dir(base="output"):
    n = 1
    while True:
        out_dir = f"{base}{n}"
        if not Path(out_dir).exists():
            Path(out_dir).mkdir(parents=True)
            return out_dir
        n += 1

def gpt_gpt_prompt(shorts_number):
    prompt = ( '''
Ты — профессиональный редактор коротких видео, работающий на фабрике контента для TikTok, YouTube Shorts и Instagram Reels.
Твоя задача — из транскрипта длинного видео (шоу, интервью, подкаст, стрим) выбрать максимально виральные, эмоциональные и самодостаточные фрагменты, которые могут набрать миллионы просмотров.
''')
    if shorts_number != 'auto':
        prompt += f"Найди ровно {shorts_number} самых подходящих фрагментов под эти критерии.\n\n"
    
    prompt += ('''
Жёсткие правила:

Длина каждого клипа: от 00:10 до 01:00.
Оптимальная длина: 20–45 секунд.
Ни один клип не должен обрываться на середине мысли или предложения.
Клип должен быть понятен без контекста всего интервью.
Если потенциальный клип получился <10 секунд, обязательно расширь его за счёт соседних реплик (вперёд или назад), сохранив смысловую цельность.

Приоритет отбора:
Эмоции — смех, шутки, сарказм, конфликты, признания.
Провокация — острые мнения, спорные формулировки, скандальные цитаты.
Цитаты и метафоры — фразы, которые легко вынести на превью.
Истории — мини-новеллы, анекдоты, рассказы.
Практическая ценность — советы, лайфхаки, правила успеха.
Сжатость — зритель должен понять суть за первые 3 секунды ролика.

Файл с транскриптом приложен (формат строк: `ss.s --> ss.s` + текст)
Ответ — СТРОГО JSON-массив:

[{"start":"SS.S","end":"SS.S","hook":"кликабельный заголовок"}]

В hook не используй начало транскрипта. Пиши готовый кликбейт-заголовок.
Убедись, что каждый клип дольше 10 секунд.
''')
    return prompt

# --- YouTube загрузка ---
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
        # Try to get a 720p stream, otherwise get the highest resolution video-only stream
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
        print(f"An error occurred with pytubefix while downloading video: {e}")
        return None

def download_audio_only(url, audio_path, lang='ru'):
    """
    Downloads audio using pytubefix and converts it for Whisper.
    Falls back to yt-dlp if pytubefix fails.
    """
    audio_path = Path(audio_path).with_suffix(".ogg")
    temp_path = audio_path.with_suffix(".temp.mp4") # pytubefix often saves audio as .mp4

    try:
        # 1. Download best audio track with pytubefix
        print("Attempting to download audio with pytubefix...")
        yt = YouTube(url)
        stream = yt.streams.get_audio_only()
        if not stream:
            raise ConnectionError("No audio stream found by pytubefix.")
        
        stream.download(output_path=str(temp_path.parent), filename=temp_path.name)
        print(f"pytubefix: Audio downloaded successfully to {temp_path}")

    except Exception as e:
        print(f"pytubefix failed: {e}. Falling back to yt-dlp for audio.")
        try:
            format_selector = f"bestaudio[lang={lang}]/bestaudio"
            subprocess.run([
                "python3", "-m", "yt_dlp",
                "-f", format_selector,
                "--user-agent", "Mozilla/5.0",
                "-o", str(temp_path),
                url
            ], check=True)
        except subprocess.CalledProcessError as e_dlp:
            print(f"yt-dlp also failed for audio: {e_dlp}")
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

def get_highlights_from_gpt(captions_path: str = "captions.txt", audio_duration: float = 600.0, shorts_number: any = 'auto'):
    """
    Делает запрос в Responses API (модель gpt-5) с включённым File Search.
    Шаги: создаёт Vector Store, загружает .txt, прикрепляет его к Vector Store,
    затем вызывает модель. Возвращает [{"start":"HH:MM:SS","end":"HH:MM:SS","hook":"..."}].
    """
    prompt = gpt_gpt_prompt(shorts_number)

    # 1) создаём Vector Store
    vs = client.vector_stores.create(name="shorts_captions_store")

    # 2) загружаем файл и прикрепляем к Vector Store
    with open(captions_path, "rb") as f:
        uploaded = client.files.create(file=f, purpose="assistants")

    client.vector_stores.files.create(
        vector_store_id=vs.id,
        file_id=uploaded.id,
    )

    # (необязательно) подождём, пока файл проиндексируется
    # чтобы избежать пустых результатов на очень больших файлах
    _wait_vector_store_ready(vs.id)

    # 3) вызываем Responses API с подключённым file_search и нашим vector_store
    resp = client.responses.create(
        model="gpt-5",
        input=[{"role": "user", "content": prompt}],
        tools=[{
            "type": "file_search",
            "vector_store_ids": [vs.id],
        }],
    )

    raw = _response_text(resp)
    json_str = _extract_json_array(raw)
    data = json.loads(json_str)

    # как и раньше: SS.S -> HH:MM:SS.S, +0.5 сек к end
    items = [{
        "start": format_seconds_to_hhmmss(float(it["start"])),
        "end":   format_seconds_to_hhmmss(float(it["end"])),
        "hook":  it["hook"]
    } for it in data]

    return items

def _wait_vector_store_ready(vector_store_id: str, timeout_s: int = 30, poll_s: float = 1.0):
    """
    Простая подстраховка: ждём, пока в хранилище появятся проиндексированные файлы.
    Если ваш SDK даёт доступ к file_counts — используем его; иначе просто спим немного.
    """
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            vs = client.vector_stores.retrieve(vector_store_id=vector_store_id)
            # в новых SDK часто есть vs.file_counts.completed
            fc = getattr(vs, "file_counts", None)
            completed = getattr(fc, "completed", None) if fc else None
            if isinstance(completed, int) and completed > 0:
                return
        except Exception:
            pass
        time.sleep(poll_s)

# ===== вспомогательные функции =====

def _response_text(resp) -> str:
    """
    Аккуратно достает текст из ответа Responses API в разных форматах/версиях SDK.
    Приоритет: output_text -> output[..].content[..].text -> fallback в str(resp).
    """
    # 1) Новый SDK зачастую имеет удобное свойство:
    if hasattr(resp, "output_text") and resp.output_text:
        return resp.output_text.strip()

    # 2) Универсальный разбор content-блоков
    try:
        output = getattr(resp, "output", None)
        if isinstance(output, list) and output:
            # берем первый item
            item = output[0]
            content = getattr(item, "content", None) or item.get("content")
            if isinstance(content, list):
                buf = []
                for c in content:
                    # в новых версиях текст лежит в c.get("text")
                    t = c.get("text") if isinstance(c, dict) else getattr(c, "text", None)
                    if isinstance(t, dict) and "value" in t:
                        buf.append(t["value"])
                    elif isinstance(t, str):
                        buf.append(t)
                if buf:
                    return "\n".join(buf).strip()
    except Exception:
        pass

    # 3) Фолбэк
    return str(resp)


def _extract_json_array(text: str) -> str:
    start = text.find('[')
    if start == -1:
        raise ValueError("В ответе GPT не найден JSON-массив.")
    depth = 0; in_str = False; esc = False
    for i, ch in enumerate(text[start:], start=start):
        if in_str:
            if esc: esc = False
            elif ch == '\\': esc = True
            elif ch == '"': in_str = False
        else:
            if ch == '"': in_str = True
            elif ch == '[': depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    return text[start:i+1]
    raise ValueError("Не удалось извлечь JSON-массив из ответа GPT.")

def get_box_center(box):
    x, y, w, h = box
    return (x + w/2, y + h/2)

def distance(p1, p2):
    return ((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)**0.5

def create_face_tracked_clip(main_clip_raw, target_height, target_width):
    """
    Creates a clip with face tracking. The frame only moves if the speaker's face
    is about to leave the visible cropped area.
    """
    main_clip_resized = main_clip_raw.resize(height=target_height)
    
    if main_clip_resized.w <= target_width:
        return main_clip_resized

    try:
        face_cascade = cv2.CascadeClassifier('haarcascade_frontalface_default.xml')
    except Exception as e:
        print(f"Could not load face cascade model: {e}. Falling back to center crop.")
        return main_clip_resized.fx(vfx.crop, x_center=main_clip_resized.w / 2, width=target_width)

    subclips = []
    
    crop_x_center = main_clip_resized.w / 2
    target_crop_x_center = main_clip_resized.w / 2
    crop_half_width = target_width / 2
    tracked_face_box = None

    # Smoothing factor for camera movement. Lower value = smoother/slower.
    smoothing_factor = 1.0

    step = 0.25  # Process video in chunks of 0.25 seconds
    for t in np.arange(0, main_clip_resized.duration, step):
        frame = main_clip_resized.get_frame(t)
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        
        faces = face_cascade.detectMultiScale(gray, 1.1, 5, minSize=(80, 80))
        is_new_face = False

        if len(faces) > 0:
            if tracked_face_box is None:
                # Case 1: No face was tracked before. This is a new face.
                tracked_face_box = sorted(faces, key=lambda f: f[2]*f[3], reverse=True)[0]
                is_new_face = True
            else:
                # Case 2: A face was being tracked. Find it in the new frame.
                previous_center = get_box_center(tracked_face_box)
                closest_face = min(faces, key=lambda f: distance(get_box_center(f), previous_center))
                
                max_allowed_distance = tracked_face_box[2] * 1.5
                if distance(get_box_center(closest_face), previous_center) < max_allowed_distance:
                    # The same face is still being tracked.
                    tracked_face_box = closest_face
                else:
                    # Case 3: The old face was lost. This is a new face.
                    tracked_face_box = sorted(faces, key=lambda f: f[2]*f[3], reverse=True)[0]
                    is_new_face = True

            face_center_x, _ = get_box_center(tracked_face_box)
            
            if is_new_face:
                # INSTANT JUMP: A new face appeared, so snap the camera immediately.
                crop_x_center = face_center_x
                target_crop_x_center = face_center_x
            else:
                # SMOOTH PAN: The same face is moving. Only adjust if it nears the edge.
                face_width = tracked_face_box[2]
                visible_left = crop_x_center - crop_half_width
                visible_right = crop_x_center + crop_half_width
                buffer = face_width * 0.5

                if not (visible_left + buffer < face_center_x < visible_right - buffer):
                    target_crop_x_center = face_center_x
        else:
            # No faces detected, lose track.
            tracked_face_box = None

        # Apply smoothing towards the target. 
        # If no move is needed, target equals current, so crop_x_center stays put.
        # If it's a new face, this will have no effect since crop_x_center was already snapped.
        crop_x_center = (smoothing_factor * target_crop_x_center) + ((1 - smoothing_factor) * crop_x_center)

        # Clamp the crop_x_center to avoid black bars
        min_x = crop_half_width
        max_x = main_clip_resized.w - crop_half_width
        clamped_crop_x_center = max(min_x, min(crop_x_center, max_x))
        crop_x_center = clamped_crop_x_center
        target_crop_x_center = max(min_x, min(target_crop_x_center, max_x))

        subclip_end = min(t + step, main_clip_resized.duration)
        subclip = main_clip_resized.subclip(t, subclip_end)
        cropped_subclip = subclip.fx(vfx.crop, x_center=clamped_crop_x_center, width=target_width)
        subclips.append(cropped_subclip.without_audio())

    if not subclips:
        return main_clip_resized.fx(vfx.crop, x_center=main_clip_resized.w / 2, width=target_width)

    final_video = concatenate_videoclips(subclips)
    final_video.audio = main_clip_raw.audio
    return final_video

def _build_video_canvas(layout, main_clip_raw, bottom_video_path, final_width, final_height):
    if layout == 'square_top_brainrot_bottom':
        video_height = int(final_height * 0.6)
        bottom_height = final_height - video_height

        main_clip = create_face_tracked_clip(main_clip_raw, video_height, final_width)

        if bottom_video_path:
            full_bottom_clip = VideoFileClip(str(bottom_video_path))
            if full_bottom_clip.duration > main_clip.duration:
                random_start = random.uniform(0, full_bottom_clip.duration - main_clip.duration)
                bottom_clip = full_bottom_clip.subclip(random_start, random_start + main_clip.duration)
            else:
                bottom_clip = full_bottom_clip
            bottom_clip = bottom_clip.resize(height=bottom_height)
            if bottom_clip.w > final_width:
                bottom_clip = bottom_clip.fx(vfx.crop, x_center=bottom_clip.w / 2, width=final_width)
            bottom_clip = bottom_clip.set_duration(main_clip.duration)
        else:
            bottom_clip = ColorClip(size=(final_width, bottom_height), color=(0,0,0), duration=main_clip.duration)

        video_canvas = clips_array([[main_clip], [bottom_clip]])
        subtitle_y_pos = video_height - 60 # Сдвигаем субтитры вверх
        subtitle_width = final_width - 40

    elif layout == 'full_top_brainrot_bottom':
        # Layout heights: 50/50 split
        video_container_height = final_height / 2
        bottom_height = final_height / 2

        # Main clip preparation
        main_clip = main_clip_raw.resize(width=final_width)

        # Position main_clip at the bottom of the top half
        main_clip_y_pos = video_container_height - main_clip.h
        main_clip = main_clip.set_position(('center', main_clip_y_pos))

        # Bottom clip preparation
        if bottom_video_path:
            full_bottom_clip = VideoFileClip(str(bottom_video_path))
            if full_bottom_clip.duration > main_clip.duration:
                random_start = random.uniform(0, full_bottom_clip.duration - main_clip.duration)
                bottom_clip = full_bottom_clip.subclip(random_start, random_start + main_clip.duration)
            else:
                bottom_clip = full_bottom_clip

            bottom_clip = bottom_clip.resize(height=bottom_height)
            if bottom_clip.w > final_width:
                bottom_clip = bottom_clip.fx(vfx.crop, x_center=bottom_clip.w / 2, width=final_width)
            bottom_clip = bottom_clip.set_duration(main_clip.duration)
        else:
            bottom_clip = ColorClip(size=(final_width, bottom_height), color=(0,0,0), duration=main_clip.duration)

        bottom_clip = bottom_clip.set_position(('center', 'bottom'))

        # Create final canvas
        bg = ColorClip(size=(final_width, final_height), color=(0,0,0), duration=main_clip.duration)
        video_canvas = CompositeVideoClip([bg, main_clip, bottom_clip])

        # Subtitles position
        subtitle_y_pos = video_container_height - 60
        subtitle_width = final_width - 40

    elif layout == 'full_center':
        main_clip = main_clip_raw.resize(width=final_width)
        bg = ColorClip(size=(final_width, final_height), color=(0,0,0), duration=main_clip.duration)
        video_canvas = CompositeVideoClip([bg, main_clip.set_position('center', 'center')])
        subtitle_y_pos = (final_height + main_clip.h) / 2 + 20
        subtitle_width = main_clip.w - 40

    elif layout == 'face_track_9_16':
        main_clip = create_face_tracked_clip(main_clip_raw, final_height, final_width)
        video_canvas = main_clip
        subtitle_y_pos = final_height * 0.75
        subtitle_width = final_width - 40

    else: # square_center
        video_height = int(final_height * 0.7)
        
        main_clip = create_face_tracked_clip(main_clip_raw, video_height, final_width)
        
        bg = ColorClip(size=(final_width, final_height), color=(0,0,0), duration=main_clip.duration)
        video_canvas = CompositeVideoClip([bg, main_clip.set_position('center', 'center')])
        subtitle_y_pos = final_height * 0.75
        subtitle_width = main_clip.w - 40
    
    return video_canvas, subtitle_y_pos, subtitle_width

def process_video_clips(config, video_path, audio_path, shorts_timecodes, transcript_segments, out_dir, send_video_callback=None):
    final_width = 720
    final_height = 1280
    futures = []

    # --- Настройки из конфига ---
    subtitle_style = config.get('subtitle_style', 'white')
    layout = config.get('layout', 'square_top_brainrot_bottom')
    bottom_video_path = config.get('bottom_video_path')
    subtitles_type = config.get('subtitles_type', 'word-by-word')

    faster_whisper_model = None

    # --- Инициализация faster-whisper (если нужно) ---
    if subtitles_type == 'word-by-word':
        faster_whisper_model = WhisperModel("base", device="cpu", compute_type="int8")

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
            layout, main_clip_raw, bottom_video_path, final_width, final_height
        )

        if subtitles_type != 'no_subtitles':
            # --- Создание и наложение субтитров ---
            subtitle_items = get_subtitle_items(
                subtitles_type, current_transcript_segments, audio_path, start_cut, end_cut, 
                faster_whisper_model)
            subtitle_clips = create_subtitle_clips(subtitle_items, subtitle_y_pos, subtitle_width, subtitle_style)
            final_clip = CompositeVideoClip([video_canvas] + subtitle_clips)
        else:
            final_clip = video_canvas
        final_clip = final_clip.set_duration(video_canvas.duration)
        final_clip.write_videofile(str(output_sub), fps=24, codec="libx264", audio_codec="aac")
        print(f"✅ Создан файл {output_sub}")
        
        # Call the callback to send the video
        if send_video_callback:
            future = send_video_callback(file_path=output_sub, hook=short["hook"], start=short["start"], end=short["end"])
            if future:
                futures.append(future)
    return futures

def main(url, config, status_callback=None, send_video_callback=None, deleteOutputAfterSending=False, user_balance: int = None):

    # --- Обработка конфига ---
    video_map = {
        'gta': './keepers/gta.mp4',
        'minecraft': './keepers/minecraft_parkur.mp4'
    }
    config['bottom_video_path'] = video_map.get(config['bottom_video'])

    out_dir = get_unique_output_dir() 
    # out_dir = './output1'
    
    print("Скачиваем видео с YouTube...")
    # скачиваем видео
    video_only = download_video_only(url, Path(out_dir) / "video_only.mp4")
    
    # скачиваем аудио
    audio_lang = config.get('audio_lang', 'ru')
    audio_only = download_audio_only(url, Path(out_dir) / "audio_only.ogg", lang=audio_lang)
    # audio_only = Path(out_dir) / "audio_only.ogg"

    if not video_only or not audio_only:
        raise Exception("Произошла ошибка при скачивании видео – мы уже о ней знаем и совсем скоро всё починим!")

    # Объединяем видео и аудио
    video_full = merge_video_audio(video_only, audio_only, Path(out_dir) / "video.mp4")
    # video_full = Path(out_dir) / "video.mp4"

    if status_callback:
        status_callback("🔍 Анализируем видео...")
    print("Транскрибируем видео...")
    force_ai_transcription = config.get('force_ai_transcription', False)
    # transcript_segments = []
    transcript_segments, lang_code = get_transcript_segments_and_file(url, out_dir=Path(out_dir), audio_path=(Path(out_dir) / "audio_only.ogg"), force_whisper=force_ai_transcription)

    if not transcript_segments:
        print("Не удалось получить транскрипцию.")
        return 0, 0
    
    # Получение смысловых кусков через GPT
    print("Ищем смысловые куски через GPT...")
    shorts_number = config.get('shorts_number', 'auto')
    # shorts_timecodes = [
    #    { "start": '00:01:49.0', "end": "00:02:10.0", "hook": "Деньги должны стать божеством" }
    # ]
    shorts_timecodes = get_highlights_from_gpt(Path(out_dir) / "captions.txt", get_audio_duration(audio_only), shorts_number=shorts_number)
    
    if not shorts_timecodes:
        print("GPT не смог выделить подходящие отрезки для шортсов.")
        if status_callback:
            status_callback("GPT не смог выделить подходящие отрезки для шортсов.")
        return 0, 0

    if user_balance is None:
        user_balance = len(shorts_timecodes)

    num_to_process = min(len(shorts_timecodes), user_balance)
    shorts_to_process = shorts_timecodes[:num_to_process]
    extra_found = len(shorts_timecodes) - num_to_process

    if status_callback:
        status_callback(f"🔥 Найдены отрезки для шортсов - {len(shorts_timecodes)} шт. Создаем {num_to_process} коротких роликов...")
    print(f"Найденные отрезки для шортсов ({len(shorts_timecodes)}):", shorts_timecodes)

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

    # если всё ок, можно удалить временный аудиофайл
    if os.path.exists(audio_only):
        try: os.remove(audio_only)
        except OSError: pass
    
    if deleteOutputAfterSending:
        shutil.rmtree(out_dir)
        print(f"🗑️ Папка {out_dir} удалена.")

    return successful_sends, extra_found





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