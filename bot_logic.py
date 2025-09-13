# -*- coding: utf-8 -*-

import os
import shutil
from dotenv import load_dotenv
from pathlib import Path

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
)
import json
from openai import OpenAI
import time
import tempfile
import re
from faster_whisper import WhisperModel
from transcription import get_transcript_segments_and_file, get_audio_duration
from subtitles import create_subtitle_clips, get_subtitle_items


client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

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

# --- YouTube загрузка ---
def download_video_only(url, video_path):
    subprocess.run([
        "python3", "-m", "yt_dlp",
        "-f", "bestvideo[height<=720]",
        "--user-agent", "Mozilla/5.0",
        "-o", str(video_path),
        url
    ])
    return video_path

def download_audio_only(url, audio_path):
    """
    Скачивает аудио с YouTube и конвертирует в мини-файл для Whisper-1:
    - формат: .ogg
    - кодек: opus
    - моно
    - частота дискретизации: 24 kHz
    - битрейт: 32 kbps
    """

    audio_path = Path(audio_path).with_suffix(".ogg")
    temp_path = audio_path.with_suffix(".temp.m4a")

    # 1. Скачиваем лучший аудиотрек
    subprocess.run([
        "python3", "-m", "yt_dlp",
        "-f", "bestaudio",
        "--user-agent", "Mozilla/5.0",
        "-o", str(temp_path),
        url
    ], check=True)

    # 2. Конвертируем в мини-файл .ogg для Whisper-1
    subprocess.run([
        "ffmpeg",
        "-i", str(temp_path),
        "-ac", "1",          # моно
        "-ar", "24000",      # частота дискретизации
        "-c:a", "libopus",   # кодек Opus
        "-b:a", "32k",       # битрейт
        "-y",
        str(audio_path)
    ], check=True)

    # 3. Удаляем временный скачанный файл
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
    prompt = (
        "Ты — профессиональный монтажёр коротких видео для TikTok, YouTube Shorts и Reels.\n"
        "Из транскрипта выбери цельные, виральные фрагменты длительностью 20–60 сек (оптимум 30–45).\n\n"
    )

    if shorts_number != 'auto':
        prompt += f"Найди ровно {shorts_number} самых подходящих фрагментов под эти критерии.\n\n"

    prompt += (
        "Жёсткие правила:\n"
        "• Фрагмент самодостаточен (начало–развитие–завершение), не дроби на мелкие фразы.\n"
        "• Если кусок <15 сек — расширь за счёт соседних реплик.\n"
        "• Бери острые мнения/конфликты, эмоции/сарказм/шутки, меткие цитаты, мини-истории/признания, советы/лайфхаки.\n"
        "• В первые 3 сек — «зацепка».\n"
        "Файл с транскриптом приложен (формат строк: `ss.s --> ss.s` + текст).\n"
        "Ответ — СТРОГО JSON-массив:\n"
        "[{\"start\":\"SS.S\",\"end\":\"SS.S\",\"hook\":\"кликабельный заголовок\"}]"
    )

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
            "vector_store_ids": [vs.id],   # <-- сюда id нашего Vector Store
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

def _build_video_canvas(layout, main_clip_raw, bottom_video_path, final_width, final_height):
    if layout == 'top_bottom':
        video_height = int(final_height * 0.6)
        bottom_height = final_height - video_height

        main_clip = main_clip_raw.resize(height=video_height)
        if main_clip.w > final_width:
            main_clip = main_clip.fx(vfx.crop, x_center=main_clip.w / 2, width=final_width)

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

    else: # main_only layout
        video_height = int(final_height * 0.7)
        main_clip = main_clip_raw.resize(height=video_height)
        if main_clip.w > final_width:
            main_clip = main_clip.fx(vfx.crop, x_center=main_clip.w / 2, width=final_width)
        
        bg = ColorClip(size=(final_width, final_height), color=(0,0,0), duration=main_clip.duration)
        video_canvas = CompositeVideoClip([bg, main_clip.set_position('center', 'center')])
        subtitle_y_pos = final_height * 0.75
        subtitle_width = main_clip.w - 40
    
    return video_canvas, subtitle_y_pos, subtitle_width

def process_video_clips(config, video_path, audio_path, shorts_timecodes, transcript_segments, out_dir, send_video_callback=None, lang_code="ru"):
    final_width = 720
    final_height = 1280
    futures = []

    # --- Настройки из конфига ---
    subtitle_style = config.get('subtitle_style', 'white')
    layout = config.get('layout', 'top_bottom')
    bottom_video_path = config.get('bottom_video_path')
    subtitles_type = config.get('subtitles_type', 'word-by-word')

    faster_whisper_model = None

    if subtitle_style == 'yellow':
        text_color = '#EDFF03'
    else:
        text_color = 'white'

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

        # --- Создание и наложение субтитров ---
        subtitle_items = get_subtitle_items(
            subtitles_type, current_transcript_segments, audio_path, start_cut, end_cut, 
            faster_whisper_model, lang_code=lang_code        )
        subtitle_clips = create_subtitle_clips(subtitle_items, subtitle_y_pos, subtitle_width, text_color)


        final_clip = CompositeVideoClip([video_canvas] + subtitle_clips)
        final_clip = final_clip.set_duration(video_canvas.duration)
        final_clip.write_videofile(str(output_sub), fps=24, codec="libx264", audio_codec="aac")
        print(f"✅ Создан файл {output_sub}")
        
        # Call the callback to send the video
        if send_video_callback:
            future = send_video_callback(file_path=output_sub, hook=short["hook"], start=short["start"], end=short["end"])
            if future:
                futures.append(future)
    return futures

def main(url, config, status_callback=None, send_video_callback=None, deleteOutputAfterSending=False):

    # --- Обработка конфига ---
    video_map = {
        'gta': './keepers/gta.mp4',
        'minecraft': './keepers/minecraft_parkur.mp4'
    }
    config['bottom_video_path'] = video_map.get(config['bottom_video'])

    out_dir = get_unique_output_dir() 
    
    if status_callback:
        status_callback("Скачиваем видео с YouTube...")
    print("Скачиваем видео с YouTube...")
    # скачиваем видео
    video_only = download_video_only(url, Path(out_dir) / "video_only.mp4")
    
    # скачиваем аудио
    audio_only = download_audio_only(url, Path(out_dir) / "audio_only.ogg")

    # Объединяем видео и аудио
    video_full = merge_video_audio(video_only, audio_only, Path(out_dir) / "video.mp4")

    if status_callback:
        status_callback("Анализируем видео...")
    print("Транскрибируем видео...")
    force_ai_transcription = config.get('force_ai_transcription', False)
    transcript_segments, lang_code = get_transcript_segments_and_file(url, out_dir=Path(out_dir), audio_path=(Path(out_dir) / "audio_only.ogg"), force_whisper=force_ai_transcription)

    if not transcript_segments:
        print("Не удалось получить транскрипцию.")
        return [] # Return empty list for consistency
    
    # Получение смысловых кусков через GPT
    print("Ищем смысловые куски через GPT...")
    shorts_number = config.get('shorts_number', 'auto')
    shorts_timecodes = get_highlights_from_gpt(Path(out_dir) / "captions.txt", get_audio_duration(audio_only), shorts_number=shorts_number)
    
    if not shorts_timecodes:
        print("GPT не смог выделить подходящие отрезки для шортсов.")
        if status_callback:
            status_callback("GPT не смог выделить подходящие отрезки для шортсов.")
        return [] # Return empty list for consistency
    if status_callback:
        status_callback(f"Найдены отрезки для шортсов - {len(shorts_timecodes)} шт. Создаю короткие ролики...")
    print(f"Найденные отрезки для шортсов ({len(shorts_timecodes)}):", shorts_timecodes)

    futures = process_video_clips(config, video_full, audio_only, shorts_timecodes, transcript_segments, out_dir, send_video_callback, lang_code=lang_code)
    
    if futures:
        for future in futures:
            future.result() # Ждем завершения отправки

    # если всё ок, можно удалить временный аудиофайл
    if os.path.exists(audio_only):
        try: os.remove(audio_only)
        except OSError: pass
    
    if deleteOutputAfterSending:
        shutil.rmtree(out_dir)
        print(f"🗑️ Папка {out_dir} удалена.")

    return [] # No longer returning a list of results, but an empty list for consistency


if __name__ == "__main__":
    url = "https://www.youtube.com/watch?v=2IaQdDjxViU"
    # ================== КОНФИГУРАЦИЯ ==================
    config = {
        # Опции: 'white', 'yellow'
        'subtitle_style': 'yellow',
        
        # Опции: 'gta', 'minecraft' или None для черного фона
        'bottom_video': 'minecraft', 
        
        # Опции: 'top_bottom', 'main_only'
        'layout': 'main_only',

        # Опции: 'word-by-word', 'phrases'
        'subtitles_type': 'word-by-word',

        # Опции: True, False
        'capitalize_sentences': True
    }
    # ================================================
    shorts = main(url, config)
