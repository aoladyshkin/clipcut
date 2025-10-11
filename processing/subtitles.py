import os
import re
import tempfile
import subprocess
import logging
from typing import List, Dict, Any, Tuple, Optional

import pysubs2
from faster_whisper import WhisperModel
from spellchecker import SpellChecker

logger = logging.getLogger(__name__)

spell = SpellChecker(language='ru')

def _correct_word(word):
    corrected = spell.correction(word)
    if corrected is None:
        return word
    return corrected

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
_TRIM_PUNCT = re.compile(r"^[^0-9A-Za-zА-Яа-яЁё-]+|[^0-9A-Za-zА-Яа-яЁё-]+$", re.UNICODE)
_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё-]+", re.UNICODE)

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

            corrected_text = _correct_word(text)
            if not corrected_text:
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
                "text": corrected_text,                          # БЕЗ пунктуации
                "start": s_clip - window_start,        # относительный старт
                "end": e_clip - window_start           # относительный конец
            })
    return items


# ============================ 
# PUBLIC: ASS РЕНДЕР
# ============================ 

def create_ass_subtitles(items, ass_path, video_width, video_height,
                         subtitle_y_pos, subtitle_width,
                         subtitle_style, subtitles_type):
    import pysubs2
    subs = pysubs2.SSAFile()
    subs.info['PlayResX'] = video_width
    subs.info['PlayResY'] = video_height
    subs.info['WrapStyle'] = 1

    COLOR_MAP = {
        'white':  '&H00FFFFFF',
        'yellow': '&H0000FFFF',
        'purple': '&H00FF00E7',
        'green':  '&H0000FF00'
    }
    c_str = COLOR_MAP.get(subtitle_style, '&H00FFFFFF').lstrip('&H')
    if len(c_str) == 8:  # AABBGGRR
        aa = int(c_str[0:2], 16); bb = int(c_str[2:4], 16); gg = int(c_str[4:6], 16); rr = int(c_str[6:8], 16)
        primary = pysubs2.Color(rr, gg, bb, aa)
    else:               # BBGGRR
        bb = int(c_str[0:2], 16); gg = int(c_str[2:4], 16); rr = int(c_str[4:6], 16)
        primary = pysubs2.Color(rr, gg, bb)

    # Базовый стиль (макс. нейтральный — всё остальное зададим тэгами)
    base = pysubs2.SSAStyle()
    base.name = "Default"
    base.fontname = "Montserrat Black"
    base.fontsize = 42
    base.bold = True
    base.primarycolor = primary
    base.secondarycolor = pysubs2.Color(255, 255, 255, 255)  # прозрачная
    base.outlinecolor  = pysubs2.Color(0, 0, 0, 0)           # прозрачная (по умолчанию)
    base.backcolor     = pysubs2.Color(0, 0, 0, 255)         # прозрачная тень
    base.outline = 0
    base.shadow  = 0
    base.align = 2
    base.marginl = base.marginr = (video_width - subtitle_width) // 2
    base.marginv = 20
    subs.styles["Default"] = base

    # Набор «теней» как в text-shadow: (bord, blur, alpha_outline)
    GLOW_LAYERS = [
        (12, 20, 0x60),
        (18, 30, 0x80),
        (24, 40, 0xA0),
        (32, 50, 0xC0),
        (40, 60, 0xD0),
    ]

    # Цвет текста для инлайна (\1c требует формат BGR, но через pysubs2 можно
    # просто оставить в стиле — мы принудительно ставим \1a/\3a/\4a дальше).
    def ass_color_bgr(c: pysubs2.Color):
        # вернём строку вида &HBBGGRR&
        return f"&H{c.b:02X}{c.g:02X}{c.r:02X}&"

    text_color_tag = f"\\1c{ass_color_bgr(primary)}"

    for item in items:
        start_ms = int(item['start'] * 1000)
        end_ms   = int(item['end'] * 1000)
        text     = item['text'].upper()

        x_center = video_width / 2
        pos_tag = rf"\an5\pos({x_center:.0f},{subtitle_y_pos:.0f})"

        animation = ""
        if subtitles_type == 'word-by-word':
            animation = r"\t(0,100,1,\fscx120\fscy120)\t(100,200,1,\fscx95\fscy95)\t(200,300,1,\fscx100\fscy100)"

        common = f"{pos_tag}{animation}"

        # ----- GLOW-слои: виден только контур (чёрный), всё остальное прозрачно -----
        # Убиваем заливку/secondary/back на уровне тэгов:
        # \1a - primary alpha, \2a - secondary alpha, \3a - outline alpha, \4a - shadow/back alpha
        for layer_idx, (bord, blur, a_out) in enumerate(GLOW_LAYERS):
            glow_text = (
                "{"
                f"{common}"
                rf"\bord{bord}\blur{blur}\xshad0\yshad0"
                r"\1a&HFF&\2a&HFF&"            # fill и secondary полностью прозрачны
                fr"\3a&H{a_out:02X}&\3c&H000000&"  # контур чёрный с заданной альфой
                r"\4a&HFF&"                    # back/shadow тоже прозрачен
                "}"
                + text
            )
            subs.append(pysubs2.SSAEvent(start=start_ms, end=end_ms,
                                         text=glow_text, style="Default", layer=layer_idx))

        # ----- Основной текст: видна только заливка (фиолетовый), без контура/тени -----
        main_text = (
            "{"
            f"{common}"
            r"\bord0\shad0\blur0\xshad0\yshad0"
            + text_color_tag +
            r"\1a&H00&\2a&HFF&\3a&HFF&\4a&HFF&"   # fill видимая, всё остальное прозрачно
            "}"
            + text
        )
        subs.append(pysubs2.SSAEvent(start=start_ms, end=end_ms,
                                     text=main_text, style="Default", layer=len(GLOW_LAYERS)+1))

    subs.save(ass_path)
    return ass_path





# ============================ 
# PUBLIC: ОСНОВНАЯ ФУНКЦИЯ
# ============================ 

def get_subtitle_items(subtitles_type: str,
                       transcript_segments: List[Dict[str, Any]],
                       audio_path: str,
                       start_cut: float,
                       end_cut: float,
                       faster_whisper_model: WhisperModel) -> List[Dict[str, Any]]:
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