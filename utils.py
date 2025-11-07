from localization import get_translation

def pluralize(n, forms):
    """
    Russian pluralization helper.
    e.g. pluralize(5, ('генерация', 'генерации', 'генераций'))
    """
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    elif 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 >= 20):
        return forms[1]
    else:
        return forms[2]

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

    if is_demo:
        balance_text = ""
    elif balance is not None:
        balance_text = get_translation(lang, 'your_balance_generations').format(balance=balance)
    else:
        balance_text = ""

    settings_text = (
        get_translation(lang, 'format_layout').format(layout=layout_map.get(config.get('layout'), get_translation(lang, 'not_selected')))
    )

    layout = config.get('layout')
    if layout in ['square_top_brainrot_bottom', 'face_track_9_16', 'square_center']:
        use_tracking = config.get('use_face_tracking', False)
        status = get_translation(lang, "enabled") if use_tracking else get_translation(lang, "disabled")
        settings_text += get_translation(lang, "face_tracking_status").format(status=status)

    settings_text += get_translation(lang, 'brainrot_status').format(status=video_map.get(config.get('bottom_video'), get_translation(lang, 'disabled'))) 
    settings_text += get_translation(lang, 'subtitles_status').format(status=sub_type_map.get(config.get('subtitles_type'), get_translation(lang, 'not_selected')))
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
