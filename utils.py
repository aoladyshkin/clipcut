from localization import get_translation

def format_config(config, balance=None, is_demo=False, lang='ru'):
    layout_map = {
        'square_center': '1:1',
        'square_top_brainrot_bottom': '1:1 + brainrot',
        'full_center': '16:9',
        'full_top_brainrot_bottom': '16:9 + brainrot',
        'face_track_9_16': '9:16'
    }
    video_map = {'gta': 'GTA', 'minecraft': 'Minecraft', None: get_translation(lang, 'none')}
    sub_type_map = {'word-by-word': get_translation(lang, 'word_by_word'), 'phrases': get_translation(lang, 'by_phrase'), 'no_subtitles': get_translation(lang, 'no_subtitles')}
    sub_style_map = {'white': get_translation(lang, 'white'), 'yellow': get_translation(lang, 'yellow'), 'purple': get_translation(lang, 'purple'), 'green': get_translation(lang, 'green'), None: get_translation(lang, 'none')}
    shorts_number = config.get('shorts_number', get_translation(lang, 'auto'))

    if shorts_number != 'auto':
        shorts_number_text = str(shorts_number)
    else:
        shorts_number_text = get_translation(lang, 'auto_select_best_fragments')

    if is_demo:
        balance_text = ""
    elif balance is not None:
        balance_text = get_translation(lang, 'your_balance_shorts').format(balance=balance)
    else:
        balance_text = ""

    settings_text = (
        get_translation(lang, 'shorts_quantity').format(shorts_number_text=shorts_number_text) +
        get_translation(lang, 'format_layout').format(layout=layout_map.get(config.get('layout'), get_translation(lang, 'not_selected'))) +
        get_translation(lang, 'brainrot_status').format(status=video_map.get(config.get('bottom_video'), get_translation(lang, 'disabled'))) +
        get_translation(lang, 'subtitles_status').format(status=sub_type_map.get(config.get('subtitles_type'), get_translation(lang, 'not_selected')))
    )
    if config.get('subtitles_type') != 'no_subtitles':
        settings_text += get_translation(lang, 'subtitle_color_status').format(color=sub_style_map.get(config.get('subtitle_style'), get_translation(lang, 'not_selected')))
        
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
