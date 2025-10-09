# -*- coding: utf-8 -*- 

import os
import json
import time
import logging
from openai import OpenAI
from config import OPENAI_API_KEY
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

В hook не используй начало транскрипта. Пиши готовый кликбейт-заголовок на том языке, на котором написана транскрипция.
Убедись, что каждый клип дольше 10 секунд.
''')
    return prompt

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

