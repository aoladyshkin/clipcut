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

VECTOR_STORE_NAME = "ShortsFactory Main Store"

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

Файл с транскриптом приложен. Формат строк в файле: `ss.s --> ss.s` + реплика.
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
        prompt += f"Выбери до {MAX_SHORTS_PER_VIDEO} таких фрагментов. Оптимально ~{MAX_SHORTS_PER_VIDEO//2} шт.\n\n"
    prompt += f'''
Длительность всего видео: {max_duration} секунд.

Правила:
1.  Каждый шортс должен длиться от 30 до 60 секунд.
2.  Шортсы не должны пересекаться.
3.  Выдай СТРОГО JSON-массив таймкодов.

Пример ответа:
[
  {{"start": "123.5", "end": "161.0"}},
  {{"start": "315.2", "end": "347.8"}}
]
'''
    return prompt

def get_random_highlights_from_gpt(shorts_number, audio_duration):
    """
    Запасной вариант: если GPT не вернул JSON, генерируем случайные таймкоды.
    """
    logger.info("Запускаю фолбэк-механизм для генерации случайных шортсов.")
    prompt = gpt_fallback_prompt(shorts_number, audio_duration)
    
    try:
        resp = client.responses.create(
            model="gpt-5-nano", # Используем более быструю и дешевую модель для фолбэка
            input=[{"role": "user", "content": prompt}],
        )
        raw = _response_text(resp)
        json_str = _extract_json_array(raw)
        data = json.loads(json_str)
        
        # Добавляем пустой hook, чтобы структура данных была одинаковой
        for item in data:
            item['hook'] = "" 
            
        return data
    except Exception as e:
        logger.error(f"Фолбэк-механизм также не удался: {e}")
        return None

def get_highlights_from_gpt(captions_path: str = "captions.txt", audio_duration: float = 600.0, shorts_number: any = 'auto'):
    """
    Использует постоянное векторное хранилище, автоматически находя его или создавая.
    """
    prompt = gpt_gpt_prompt(shorts_number)
    data = None
    is_fallback = False
    vs = None
    uploaded_file = None

    try:
        # 1) Ищем существующее хранилище или создаем новое
        vector_stores = client.vector_stores.list()
        for store in vector_stores.data:
            if store.name == VECTOR_STORE_NAME:
                vs = store
                logger.info(f"Использую существующее хранилище: {vs.id}")
                break
        
        if not vs:
            logger.info(f"Создаю новое постоянное хранилище '{VECTOR_STORE_NAME}'...")
            vs = client.vector_stores.create(name=VECTOR_STORE_NAME)

        # 2) Загружаем файл и прикрепляем к постоянному Vector Store
        with open(captions_path, "rb") as f:
            uploaded_file = client.files.create(file=f, purpose="assistants")

        client.vector_stores.files.create(
            vector_store_id=vs.id,
            file_id=uploaded_file.id,
        )

        # 3) Ждём, пока файл будет готов к использованию
        _wait_for_file_indexing(vs.id, uploaded_file.id)

        # 4) Вызываем ассистента
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

    except (ValueError, TimeoutError) as e:
        logger.warning(f"Основной метод выбора хайлайтов не удался ({e.__class__.__name__}: {e}). Переключаюсь на фолбэк.")
        caption_segments = _parse_captions(captions_path)
        if not caption_segments:
            raise ValueError("Не удалось спарсить субтитры для фолбэка.")

        max_duration = 0
        if caption_segments:
            max_duration = max(seg['end'] for seg in caption_segments)

        data = get_random_highlights_from_gpt(shorts_number, max_duration)
        if data is None:
            raise ValueError("Фолбэк-механизм также не смог сгенерировать таймкоды.")
        is_fallback = True
    
    finally:
        # Удаляем только временный файл, а не все хранилище
        if vs and uploaded_file:
            try:
                logger.info(f"Удаляю файл {uploaded_file.id} из хранилища {vs.id}...")
                client.vector_stores.files.delete(vector_store_id=vs.id, file_id=uploaded_file.id)
                logger.info(f"Удаляю файл {uploaded_file.id} из общего хранилища...")
                client.files.delete(file_id=uploaded_file.id)
            except Exception as delete_e:
                logger.error(f"Ошибка при удалении файла {uploaded_file.id}: {delete_e}")

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
            "hook":  it["hook"]
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
