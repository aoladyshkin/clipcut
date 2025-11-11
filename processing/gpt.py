# -*- coding: utf-8 -*- 

import os
import re
import json
import time
import logging
from openai import OpenAI
from config import OPENAI_API_KEY, MAX_SHORTS_PER_VIDEO
from utils import format_seconds_to_hhmmss

client = OpenAI(api_key=OPENAI_API_KEY)
logger = logging.getLogger(__name__)

def gpt_gpt_prompt(shorts_number):
    prompt = ( '''
Ты — профессиональный редактор коротких видео, работающий на фабрике контента для TikTok, YouTube Shorts и Instagram Reels.
Твоя задача — из транскрипта длинного видео (шоу, интервью, подкаст, стрим) выбрать максимально виральные, эмоциональные и самодостаточные фрагменты, которые могут набрать миллионы просмотров.
''')
    
    if shorts_number != 'auto':
        prompt += f"Найди ровно {shorts_number} самых подходящих фрагментов под эти критерии.\n\n"
    else:
        prompt += f"Выбери до {MAX_SHORTS_PER_VIDEO} таких фрагментов.\n\n"
    prompt += ('''
Жёсткие правила:

Длина каждого клипа: от 00:20 до 01:00.
Оптимальная длина: 40–60 секунд.
Клип должен быть понятен без контекста всего интервью.
Если потенциальный клип получился <20 секунд, обязательно расширь его за счёт соседних реплик (вперёд или назад), сохранив смысловую цельность.

КРИТИЧЕСКИ ВАЖНО:
1.  **Не обрывай мысль.** Клип должен заканчиваться на точке, вопросительном или восклицательном знаке. Не обрывай клип на полуслове или на союзе «и», «но», «потому что» и т.д.
    *   ❌ **ПЛОХОЙ ПРИМЕР:** Клип обрывается на фразе "...и поэтому я решил, что...", не закончив мысль.
    *   ✅ **ХОРОШИЙ ПРИМЕР:** Клип заканчивается на полной фразе "...и поэтому я решил, что это был лучший день в моей жизни."
2.  **Строго соблюдай длительность.** Минимальная длина — 20 секунд, максимальная — 60 секунд. Клипы за пределами этого диапазона будут отброшены.

Приоритет отбора:
Эмоции — смех, шутки, сарказм, конфликты, признания.
Провокация — острые мнения, спорные формулировки, скандальные цитаты.
Цитаты и метафоры — фразы, которые легко вынести на превью.
Истории — мини-новеллы, анекдоты, рассказы.
Практическая ценность — советы, лайфхаки, правила успеха.
Сжатость — зритель должен понять суть за первые 3 секунды ролика.

Файл с транскриптом приложен (формат строк: `ss.s --> ss.s` + текст)
Ответ — СТРОГО JSON-массив:

[{"start":"120.5","end":"160.0","hook":"кликабельный заголовок"}]

В hook не используй начало транскрипта. Пиши готовый кликбейт-заголовок на том языке, на котором написана транскрипция.
Убедись, что каждый клип дольше 20 секунд.
''')
    return prompt

def _parse_captions(captions_path: str):
    """Парсит файл субтитров в список сегментов."""
    with open(captions_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    segments = []
    # Regex to find timestamps and text, including multi-line text
    pattern = re.compile(r'(\d+\.\d+) --> (\d+\.\d+)\n(.*?)(?=\n\d+\.\d+ -->|\Z)', re.DOTALL)
    matches = pattern.findall(content)
    
    for match in matches:
        start_time = float(match[0])
        end_time = float(match[1])
        text = match[2].strip()
        segments.append({'start': start_time, 'end': end_time, 'text': text})
        
    return segments

def gpt_fallback_prompt(shorts_number, max_duration):
    prompt = f'''
Ты — аварийный генератор таймкодов для видео. Основной AI не справился с задачей.
Твоя задача — нарезать видео на случайные, но правдоподобные фрагменты (шортсов).
'''
    if shorts_number != 'auto':
        prompt += f"Сгенерируй ровно {shorts_number} шортсов.\n\n"
    else:
        prompt += f"Выбери до {MAX_SHORTS_PER_VIDEO} таких фрагментов.\n\n"
    prompt += f'''
Длительность всего видео: {max_duration} секунд.

Правила:
1.  Каждый шортс должен длиться от 30 до 60 секунд.
2.  Шортсы не должны пересекаться.
3.  Выдай СТРОГО JSON-массив таймкодов.

Пример ответа:
[
  {{"start": "120.5", "end": "160.0"}},
  {{"start": "300.2", "end": "345.8"}}
]
'''
    return prompt

def _get_random_highlights_from_gpt(shorts_number, audio_duration):
    """
    Запасной вариант: если GPT не вернул JSON, генерируем случайные таймкоды.
    """
    logger.info("Запускаю фолбэк-механизм для генерации случайных шортсов.")
    prompt = gpt_fallback_prompt(shorts_number, audio_duration)
    
    try:
        resp = client.responses.create(
            model="gpt-4o", # Используем более быструю и дешевую модель для фолбэка
            input=[{"role": "user", "content": prompt}],
        )
        raw = _response_text(resp)
        json_str = _extract_json_array(raw)
        data = json.loads(json_str)
        
        # Добавляем фейковый hook
        for item in data:
            item['hook'] = "🔥" # Используем простой хук
            
        return data
    except Exception as e:
        logger.error(f"Фолбэк-механизм также не удался: {e}")
        return None

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

    data = None
    
    try:
        # 3) вызываем Responses API с подключённым file_search и нашим vector_store
        resp = client.responses.create(
            model="gpt-5",
            input=[{"role": "user", "content": prompt}],
            tools=[
                {
                    "type": "file_search",
                    "vector_store_ids": [vs.id],
                }
            ],
        )

        raw = _response_text(resp)
        json_str = _extract_json_array(raw)
        data = json.loads(json_str)

    except ValueError as e:
        logger.warning("Основной метод выбора хайлайтов не удался. Переключаюсь на фолбэк.")
        caption_segments = _parse_captions(captions_path)
        if not caption_segments:
            raise ValueError("Не удалось спарсить субтитры для фолбэка.")

        # Находим самый длинный монолог для определения максимальной длительности
        max_duration = 0
        if caption_segments:
            max_duration = max(seg['end'] for seg in caption_segments)

        data = _get_random_highlights_from_gpt(shorts_number, max_duration)
        if data is None:
            raise ValueError("Фолбэк-механизм также не смог сгенерировать таймкоды.")
    
    if data is None:
        raise ValueError("Не удалось получить данные от GPT ни одним из способов.")

    # --- Post-processing --- 
    caption_segments = _parse_captions(captions_path)
    processed_data = []

    for it in data:
        start_time = float(it["start"])
        end_time = float(it["end"])

        # 1. Enforce 60-second limit
        if end_time - start_time > 60.0:
            end_time = start_time + 60.0
            logger.info(f"обрезаю клип до 60 секунд: {it['hook']}")

        # 2. Adjust end time to the end of a sentence
        # Find the segment where the clip ends
        end_segment_index = -1
        for i, seg in enumerate(caption_segments):
            if seg['start'] <= end_time < seg['end']:
                end_segment_index = i
                break
        
        if end_segment_index != -1:
            # Check current and next few segments for a sentence end
            search_text = ""
            last_segment_end_time = end_time
            for i in range(end_segment_index, min(end_segment_index + 5, len(caption_segments))):
                segment = caption_segments[i]
                search_text += segment['text'] + " "
                last_segment_end_time = segment['end']
                
                # If we find a sentence end, and it's within a reasonable threshold
                if any(p in segment['text'] for p in '.!?'):
                    new_end_time = segment['end']
                    if new_end_time - end_time < 5.0: # 5-second threshold
                        end_time = new_end_time
                        logger.info(f"корректирую окончание клипа по предложению: {it['hook']}")
                        break

    
        processed_data.append({
            "start": format_seconds_to_hhmmss(start_time),
            "end":   format_seconds_to_hhmmss(end_time),
            "hook":  it["hook"]
        })

    return processed_data

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
        logger.warning(f"Ответ GPT {text}")
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


