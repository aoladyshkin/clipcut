import os
import re
import tempfile
import subprocess
import logging
from typing import List, Dict, Any, Tuple, Optional

from moviepy.editor import TextClip
from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)

# ============================
# НАСТРОЙКИ (минимум логики)
# ============================

AUDIO_PAD_SEC = 0.20                 # небольшой паддинг по краям, чтобы не терять слова на стыках
MIN_WORD_DURATION_SEC = 0.03         # отсечь сверхкороткие «слова»-артефакты
REF_SNAP_ENABLED = True              # включить пост-коррекцию к эталонному тексту
REF_SNAP_SIM_THRESHOLD = 0.62        # порог похожести (0..1) для замены на референс
TEXT_FONT = "fonts/Montserrat.ttf"   # путь к шрифту для отрисовки сабов MoviePy


# ============================
# УТИЛИТЫ
# ============================

# Разрешаем буквы/цифры/дефис. Всё остальное срезаем по краям.
_TRIM_PUNCT = re.compile(r"^[^0-9A-Za-zА-Яа-яЁё\-]+|[^0-9A-Za-zА-Яа-яЁё\-]+$", re.UNICODE)
_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё\-]+", re.UNICODE)

def _normalize_token(s: str) -> str:
    """Убираем запятые, точки, кавычки и прочие знаки по краям."""
    return _TRIM_PUNCT.sub("", s or "").strip()

def _tokenize_text(s: str) -> List[str]:
    """Достаём «слова» из текста референса (для snap)."""
    return [m.group(0) for m in _WORD_RE.finditer(s or "")]

def _levenshtein(a: str, b: str) -> int:
    a, b = (a or "").lower(), (b or "").lower()
    la, lb = len(a), len(b)
    if la == 0: return lb
    if lb == 0: return la
    prev = list(range(lb + 1))
    curr = [0] * (lb + 1)
    for i in range(1, la + 1):
        curr[0] = i
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cb = b[j - 1]
            cost = 0 if ca == cb else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev, curr = curr, prev
    return prev[lb]

def _similarity(a: str, b: str) -> float:
    m = max(len(a or ""), len(b or ""))
    if m == 0: return 1.0
    return 1.0 - (_levenshtein(a or "", b or "") / m)


# ============================
# АУДИО ВЫРЕЗКА
# ============================

def _extract_wav_pcm(audio_path: str, start_cut: float, end_cut: float, pad: float = AUDIO_PAD_SEC) -> Tuple[str, float]:
    """
    Нарезаем WAV PCM 16k mono (без потерь) с маленьким паддингом.
    Возвращаем (временный_путь, абсолютный_сдвиг_начала_куска).
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    chunk_path = tmp.name
    tmp.close()

    ss = max(0.0, start_cut - pad)
    to = end_cut + pad

    cmd = [
        "ffmpeg",
        "-ss", f"{ss:.2f}",
        "-to", f"{to:.2f}",
        "-i", str(audio_path),
        "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le",
        "-y", chunk_path
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        try: os.remove(chunk_path)
        except Exception: pass
        raise RuntimeError(f"ffmpeg failed: {res.stderr[:400]}")
    return chunk_path, ss


# ============================
# SNAP К РЕФЕРЕНСУ
# ============================

def _build_reference_tokens(transcript_segments: List[Dict[str, Any]], start_cut: float, end_cut: float) -> List[str]:
    """
    Собираем «эталонные» слова из фразовых сегментов, пересекающих текущее окно.
    """
    ref: List[str] = []
    for seg in transcript_segments:
        s = float(seg.get("start", 0.0))
        e = float(seg.get("end", 0.0))
        if s < end_cut and e > start_cut:
            for tok in _tokenize_text(str(seg.get("text", ""))):
                norm = _normalize_token(tok)
                if norm:
                    ref.append(norm)
    return ref

def _snap_items_to_reference(items: List[Dict[str, Any]], reference_tokens: List[str],
                             threshold: float = REF_SNAP_SIM_THRESHOLD) -> List[Dict[str, Any]]:
    """
    Подменяем распознанное слово на ближайшее из референса, если похоже достаточно сильно.
    Тайминги НЕ меняем.
    """
    if not reference_tokens:
        return items
    snapped: List[Dict[str, Any]] = []
    for it in items:
        src = _normalize_token(it.get("text", ""))
        best = src
        best_sim = -1.0
        for rt in reference_tokens:
            sim = _similarity(src, rt)
            if sim > best_sim:
                best_sim = sim
                best = rt
        new_text = best if best_sim >= threshold else src
        snapped.append({"text": new_text, "start": it["start"], "end": it["end"]})
    return snapped


# ============================
# ПРЕОБРАЗОВАНИЕ СЕГМЕНТОВ В СЛОВА
# ============================

def _segments_to_word_items(segments,
                            window_start: float,
                            window_end: float,
                            offset_abs: float) -> List[Dict[str, Any]]:
    """
    1 слово -> 1 item. Чистим пунктуацию (запятые/точки/кавычки),
    клиппим в окно и возвращаем относительные тайминги.
    """
    items: List[Dict[str, Any]] = []
    for seg in segments:
        if not getattr(seg, "words", None):
            continue
        for w in seg.words:
            if w.start is None or w.end is None:
                continue
            # отсекаем сверхкороткие «слова»-артефакты
            if (w.end - w.start) < MIN_WORD_DURATION_SEC:
                continue

            text = _normalize_token(w.word)
            if not text:
                continue

            s_abs = float(w.start) + offset_abs
            e_abs = float(w.end) + offset_abs
            if e_abs <= window_start or s_abs >= window_end:
                continue

            s_clip = max(s_abs, window_start)
            e_clip = min(e_abs, window_end)
            if e_clip <= s_clip:
                continue

            items.append({
                "text": text,                          # БЕЗ пунктуации
                "start": s_clip - window_start,        # относительный старт
                "end": e_clip - window_start           # относительный конец
            })
    return items


# ============================
# PUBLIC: MOVIEPY РЕНДЕР
# ============================

def create_subtitle_clips(items, subtitle_y_pos, subtitle_width, text_color):
    """
    Превращаем items в набор TextClip (тень + текст).
    """
    subtitle_clips = []
    shadow_offset = 4
    text_params = {
        "fontsize": 42,
        "font": TEXT_FONT,
        "method": "caption",
        "size": (subtitle_width, None)
    }
    for it in items:
        txt = it["text"]
        start_rel = it["start"]
        end_rel = it["end"]

        temp = TextClip(txt, **text_params)
        y_pos = subtitle_y_pos - temp.h / 2

        shadow = (TextClip(txt, color="black", **text_params)
                  .set_position(("center", y_pos + shadow_offset))
                  .set_start(start_rel).set_end(end_rel))
        text = (TextClip(txt, color=text_color, **text_params)
                .set_position(("center", y_pos))
                .set_start(start_rel).set_end(end_rel))

        subtitle_clips.extend([shadow, text])
    return subtitle_clips


# ============================
# PUBLIC: ОСНОВНАЯ ФУНКЦИЯ
# ============================

def get_subtitle_items(subtitles_type: str,
                       transcript_segments: List[Dict[str, Any]],
                       audio_path: str,
                       start_cut: float,
                       end_cut: float,
                       faster_whisper_model: WhisperModel,
                       lang_code: str = "ru") -> List[Dict[str, Any]]:
    """
    - 'word-by-word': простая транскрибация faster-whisper БЕЗ initial_prompt,
      каждое слово с таймкодом начала и конца; точки/запятые/кавычки убраны.
      Затем (если включено) пост-коррекция на основе референс-текста (snap).
    - 'phrases': как есть из transcript_segments (относительные тайминги).
    """
    items: List[Dict[str, Any]] = []

    if subtitles_type == "word-by-word":
        chunk_path, offset = _extract_wav_pcm(audio_path, start_cut, end_cut, pad=AUDIO_PAD_SEC)
        try:
            # Минимальный и стабильный вызов распознавания:
            segments, _ = faster_whisper_model.transcribe(
                chunk_path,
                language=lang_code,
                task="transcribe",
                word_timestamps=True,
                beam_size=5,
                best_of=5,
                temperature=0.0
            )

            # Слова из сегментов
            items = _segments_to_word_items(segments, start_cut, end_cut, offset)

            # Пост-коррекция к эталонному тексту (правильные окончания/падежи)
            if REF_SNAP_ENABLED and items:
                ref_tokens = _build_reference_tokens(transcript_segments, start_cut, end_cut)
                if ref_tokens:
                    items = _snap_items_to_reference(items, ref_tokens, REF_SNAP_SIM_THRESHOLD)

        finally:
            try:
                os.remove(chunk_path)
            except Exception:
                pass

    else:  # 'phrases'
        for ts in transcript_segments:
            s = float(ts.get("start", 0.0))
            e = float(ts.get("end", 0.0))
            if (s >= start_cut and e <= end_cut) or (s < start_cut < e) or (s < end_cut < e):
                items.append({
                    "text": ts.get("text", ""),
                    "start": s - start_cut,
                    "end": e - start_cut
                })

    return items
