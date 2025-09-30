def format_config(config, balance=None, is_demo=False):
    layout_map = {
        'square_center': '1:1',
        'square_top_brainrot_bottom': '1:1 + brainrot',
        'full_center': '16:9',
        'full_top_brainrot_bottom': '16:9 + brainrot',
        'face_track_9_16': '9:16'
    }
    video_map = {'gta': 'GTA', 'minecraft': 'Minecraft', None: 'нет'}
    sub_type_map = {'word-by-word': 'по одному слову', 'phrases': 'по фразе', 'no_subtitles': 'без субтитров'}
    sub_style_map = {'white': 'белый', 'yellow': 'желтый', 'purple': 'фиолетовый', 'green': 'зелёный', None: 'нет'}
    shorts_number = config.get('shorts_number', 'Авто')

    if shorts_number != 'auto':
        shorts_number_text = str(shorts_number)
        cost = shorts_number
    else:
        shorts_number_text = 'Автоматически подберём лучшие фрагменты'
        cost = 1  # Assume 1 for 'auto' for display purposes

    if is_demo:
        balance_text = ""
    elif balance is not None:
        balance_text = f"<b>Ваш баланс</b>: {balance} шортсов\n"
    else:
        balance_text = ""

    settings_text = (
        f"<b>✂️ Количество шортсов</b>: {shorts_number_text}\n"
        f"<b>📐 Формат</b>: {layout_map.get(config.get('layout'), 'Не выбрано')}\n"
        f"<b>🧠 Brainrot</b>: {video_map.get(config.get('bottom_video'), 'выключен')}\n"
        f"<b>🔤 Субтитры</b>: {sub_type_map.get(config.get('subtitles_type'), 'Не выбрано')}\n"
    )
    if config.get('subtitles_type') != 'no_subtitles':
        settings_text += f"<b>🎨 Цвет субтитров</b>: {sub_style_map.get(config.get('subtitle_style'), 'Не выбрано')}\n"
        
    settings_text += f"\n{balance_text}" if balance_text else ""

    return settings_text

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
