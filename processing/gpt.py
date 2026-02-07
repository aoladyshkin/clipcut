# -*- coding: utf-8 -*- 

import os
import re
import json
import time
import logging
import random
import math
from openai import OpenAI
from config import OPENAI_API_KEY, MAX_SHORTS_PER_VIDEO, MIN_SHORT_DURATION, MAX_SHORT_DURATION
from utils import format_seconds_to_hhmmss

client = OpenAI(api_key=OPENAI_API_KEY)
logger = logging.getLogger(__name__)


def gpt_gpt_prompt(shorts_number, video_duration_seconds=None):
    duration_str = ""
    if video_duration_seconds:
        duration_str = format_seconds_to_hhmmss(video_duration_seconds)

    prompt = (f'''
ТЫ РАБОТАЕШЬ В РЕЖИМЕ STRICT-JSON.

ЕСЛИ ТЫ ВЫВОДИШЬ ХОТЬ ОДИН СИМВОЛ ВНЕ JSON — ЭТО ОШИБКА.
ТЫ НЕ МОЖЕШЬ ПИСАТЬ ТЕКСТ, ИСПОЛЬЗОВАТЬ ИЗВИНЕНИЯ, ДИАЛОГ, ВОПРОСЫ, ПОЯСНЕНИЯ, ОЦЕНКИ.
ТЫ НЕ МОЖЕШЬ ПИСАТЬ ФРАЗЫ ТИПА "не могу", "извините", "мне нужно", "я предлагаю", "недостаточно данных".
ЕСЛИ ДАННЫХ НЕДОСТАТОЧНО — ВСЁ РАВНО СОЗДАВАЙ КЛИПЫ.
НЕЛЬЗЯ ВЫВОДИТЬ ПУСТОЙ МАССИВ.
ЕСЛИ ТЕКСТ ТРЕБУЕТ ДОПОЛНЕНИЯ — РАСШИРИ СЕГМЕНТ ПО СОСЕДНИМ РЕПЛИКАМ.

ТВОЙ ЕДИНСТВЕННЫЙ ВЫХОД — ЭТО JSON-МАССИВ.

ФОРМАТ ВЫХОДА:

[
  {{
    "start": "120.5",
    "end": "160.0",
    "hook": "кликбейтный заголовок",
    "virality_score": 9
  }}
]

НЕ ПИШИ ВНЕ JSON НИ ОДНОГО СИМВОЛА.

---
Ты — профессиональный редактор коротких видео, работающий на фабрике контента для TikTok, YouTube Shorts и Instagram Reels.
Твоя задача — из транскрипта длинного видео (шоу, интервью, подкаст, стрим) выбрать максимально виральные, эмоциональные и самодостаточные фрагменты, которые могут набрать миллионы просмотров.
{'Видео длится ' + duration_str if duration_str else ''}
''')
    
    if shorts_number != 'auto':
        prompt += f"Найди ровно {shorts_number} самых подходящих фрагментов под эти критерии.\n\n"
    else:
        prompt += f'''ОПРЕДЕЛИ КОЛИЧЕСТВО КЛИПОВ ТАК:
- если длительность видео < 10 минут → 2-3 клипа
- если 10–20 минут → 5 клипов
- если 20–300 минут → 6-7 клипов
- если 20–40 минут → 8 клипов
- если 40-70 минут → 10 клипов\n
- если >70 минут → {MAX_SHORTS_PER_VIDEO-1} клипов\n
'''
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

Файл с транскриптом приложен. Формат строк в файле: `ss.s --> ss.s` + реплика.
Для каждого фрагмента определи "оценку виральности" (virality_score) по шкале от 1 до 10, где 10 — это максимальный потенциал стать вирусным.

Ответ — СТРОГО JSON-массив:

[{"start":"120.5","end":"160.0","hook":"кликабельный заголовок","virality_score":9}]

В hook не используй начало транскрипта. Пиши готовый кликбейт-заголовок на том языке, на котором написана транскрипция.
Убедись, что каждый клип дольше 20 секунд.
ВЫВЕДИ ТОЛЬКО JSON.
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

def generate_random_shorts(audio_duration: float, shorts_number: any = 'auto') -> list:
    """
    Генерирует случайные, непересекающиеся таймкоды для шортсов.
    """
    logger.info("Генерирую случайные шортсы в качестве фолбэка.")
    
    # Определяем количество шортсов
    if shorts_number == 'auto':
        num_shorts_to_generate = random.randint(MAX_SHORTS_PER_VIDEO // 2, MAX_SHORTS_PER_VIDEO)
    else:
        num_shorts_to_generate = int(shorts_number)

    # Убедимся, что минимальное количество шортсов не превышает максимально возможное
    max_possible_shorts = math.floor(audio_duration / MIN_SHORT_DURATION)
    num_shorts_to_generate = min(num_shorts_to_generate, max_possible_shorts)

    if num_shorts_to_generate <= 0:
        logger.warning("Невозможно сгенерировать шортсы: недостаточно длины видео или некорректное количество.")
        return []

    generated_shorts = []
    attempts = 0
    max_attempts_per_short = 100 

    while len(generated_shorts) < num_shorts_to_generate and attempts < num_shorts_to_generate * max_attempts_per_short:
        # Случайная длительность шортса
        current_short_duration = random.uniform(MIN_SHORT_DURATION, MAX_SHORT_DURATION)
        
        # Случайное начало шортса
        max_start_time = audio_duration - current_short_duration
        if max_start_time < 0:
            attempts += 1
            continue # Невозможно разместить шортс такой длины

        start_time = random.uniform(0, max_start_time)
        end_time = start_time + current_short_duration

        # Проверяем на пересечения с уже сгенерированными шортсами
        is_overlapping = False
        for existing_short in generated_shorts:
            # Небольшой отступ, чтобы избежать наложения из-за float-точности
            if not (end_time + 1 <= existing_short['start'] or start_time >= existing_short['end'] + 1):
                is_overlapping = True
                break
        
        if not is_overlapping:
            generated_shorts.append({'start': round(start_time, 2), 'end': round(end_time, 2), 'hook': ""})
        else:
            attempts += 1
            
    if len(generated_shorts) < num_shorts_to_generate:
        logger.warning(f"Сгенерировано только {len(generated_shorts)} из {num_shorts_to_generate} запрошенных шортсов после {attempts} попыток.")

    # Сортируем по времени начала
    generated_shorts.sort(key=lambda x: x['start'])
    return generated_shorts


def get_random_highlights(shorts_number, audio_duration):
    """
    Запасной вариант: если GPT не вернул JSON, генерируем случайные таймкоды.
    Также добавляем убывающую оценку виральности.
    """
    logger.info("Запускаю фолбэк-механизм для генерации случайных шортсов.")
    try:
        data = generate_random_shorts(audio_duration, shorts_number)
        if not data:
            raise ValueError("Не удалось сгенерировать случайные шортсы.")
        
        # Добавляем убывающий virality_score
        num_shorts = len(data)
        for i, short in enumerate(data):
            short['virality_score'] = random.randint(5, 10)

        return data
    except Exception as e:
        logger.error(f"Фолбэк-механизм генерации случайных шортсов не удался: {e}")
        return None

def get_highlights_from_gpt(captions_path: str = "captions.txt", audio_duration: float = 600.0, shorts_number: any = 'auto'):
    """
    Creates a temporary vector store for each request to ensure isolation.
    """
    prompt = gpt_gpt_prompt(shorts_number, audio_duration)
    data = None
    is_fallback = False
    vs = None
    uploaded_file = None

    try:
        # 1) Создаем временное векторное хранилище для изоляции
        logger.info("Создаю временное векторное хранилище...")
        vs = client.vector_stores.create(name=f"Temp Store - {time.time()}")

        # 2) Загружаем файл и прикрепляем к временному хранилищу
        with open(captions_path, "rb") as f:
            uploaded_file = client.files.create(file=f, purpose="assistants")
        
        client.vector_stores.files.create(
            vector_store_id=vs.id,
            file_id=uploaded_file.id
        )

        # 3) Ждём, пока файл будет готов к использованию
        _wait_for_file_indexing(vs.id, uploaded_file.id)

        # 4) Вызываем ассистента с этим временным хранилищем
        logger.info(f"Вызываю ассистента с временным хранилищем {vs.id}...")
        resp = client.responses.create(
            model="gpt-5-nano",
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

        if not data or len(data) == 0:
            raise ValueError("GPT вернул пустой JSON-массив. Запускаю фолбэк.")

    except (ValueError, TimeoutError) as e:
        logger.warning(f"Основной метод выбора хайлайтов не удался ({e.__class__.__name__}: {e}). Переключаюсь на фолбэк.")
        caption_segments = _parse_captions(captions_path)
        if not caption_segments:
            raise ValueError("Не удалось спарсить субтитры для фолбэка.")

        max_duration = audio_duration
        data = get_random_highlights(shorts_number, max_duration)
        if data is None:
            raise ValueError("Фолбэк-механизм также не смог сгенерировать таймкоды.")
        is_fallback = True
    
    finally:
        # Очистка: удаляем временное хранилище и файл
        if uploaded_file:
            try:
                client.files.delete(file_id=uploaded_file.id)
                logger.info(f"Временный файл {uploaded_file.id} удален.")
            except Exception as delete_e:
                logger.error(f"Ошибка при удалении временного файла {uploaded_file.id}: {delete_e}")
        if vs:
            try:
                client.vector_stores.delete(vector_store_id=vs.id)
                logger.info(f"Временное хранилище {vs.id} удалено.")
            except Exception as delete_e:
                logger.error(f"Ошибка при удалении временного хранилища {vs.id}: {delete_e}")

    if data is None:
        raise ValueError("Не удалось получить данные от GPT ни одним из способов.")

    # --- Post-processing --- 
    caption_segments = _parse_captions(captions_path)
    processed_data = []

    for it in data:
        start_time = float(it["start"])
        end_time = float(it["end"])

        if not is_fallback:
            if end_time - start_time > 60.0:
                end_time = start_time + 60.0
                logger.info(f"обрезаю клип до 60 секунд: {it['hook']}")

            end_segment_index = -1
            for i, seg in enumerate(caption_segments):
                if seg['start'] <= end_time < seg['end']:
                    end_segment_index = i
                    break
            
            if end_segment_index != -1:
                search_text = ""
                for i in range(end_segment_index, min(end_segment_index + 5, len(caption_segments))):
                    segment = caption_segments[i]
                    search_text += segment['text'] + " "
                    
                    if any(p in segment['text'] for p in '.!?'):
                        new_end_time = segment['end']
                        if new_end_time - end_time < 5.0:
                            end_time = new_end_time
                            logger.info(f"корректирую окончание клипа по предложению: {it['hook']}")
                            break

    
        processed_data.append({
            "start": format_seconds_to_hhmmss(start_time),
            "end":   format_seconds_to_hhmmss(end_time),
            "hook":  it["hook"],
            "virality_score": it.get("virality_score", 5) # Извлекаем оценку, по умолчанию 5
        })

    return processed_data

def _wait_for_file_indexing(vector_store_id: str, file_id: str, timeout_s: int = 600):
    """
    Ожидает завершения индексации файла в векторном хранилище.
    """
    start_time = time.time()
    logger.info(f"Ожидание индексации файла {file_id} в хранилище {vector_store_id}...")
    
    while time.time() - start_time < timeout_s:
        try:
            file_status = client.vector_stores.files.retrieve(
                vector_store_id=vector_store_id,
                file_id=file_id
            )
            
            logger.info(f"Текущий статус файла {file_id}: {file_status.status}")

            if file_status.status == 'completed':
                logger.info(f"Файл {file_id} успешно проиндексирован.")
                return
            elif file_status.status in ['failed', 'cancelled']:
                logger.error(f"Индексация файла {file_id} не удалась со статусом: {file_status.status}.")
                raise ValueError(f"Индексация файла {file_id} не удалась.")
            
        except Exception as e:
            logger.warning(f"Не удалось получить статус индексации файла: {e}")
        
        time.sleep(5)
        
    raise TimeoutError(f"Тайм-аут ожидания индексации файла {file_id}.")

# ===== вспомогательные функции =====

def _response_text(resp) -> str:
    """
    Аккуратно достает текст из ответа Responses API в разных форматах/версиях SDK.
    """
    if hasattr(resp, "output_text") and resp.output_text:
        return resp.output_text.strip()

    try:
        output = getattr(resp, "output", None)
        if isinstance(output, list) and output:
            item = output[0]
            content = getattr(item, "content", None) or item.get("content")
            if isinstance(content, list):
                buf = []
                for c in content:
                    t = c.get("text") if isinstance(c, dict) else getattr(c, "text", None)
                    if isinstance(t, dict) and "value" in t:
                        buf.append(t["value"])
                    elif isinstance(t, str):
                        buf.append(t)
                if buf:
                    return "\n".join(buf).strip()
    except Exception:
        pass

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
