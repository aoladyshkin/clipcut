def format_config(config, balance=None):
    layout_map = {'top_bottom': 'Осн. видео + brainrot', 'main_only': 'Только основное видео'}
    video_map = {'gta': 'GTA', 'minecraft': 'Minecraft', None: 'Нет'}
    sub_type_map = {'word-by-word': 'По одному слову', 'phrases': 'По фразе', 'no_subtitles': 'Без субтитров'}
    sub_style_map = {'white': 'Белый', 'yellow': 'Желтый', None: 'Нет'}
    shorts_number = config.get('shorts_number', 'Авто')
    if shorts_number != 'auto':
        shorts_number_text = str(shorts_number)
    else:
        shorts_number_text = 'Авто'

    balance_text = f"<b>Ваш баланс</b>: {balance} шортсов\n" if balance is not None else ""

    settings_text = (
        f"{balance_text}\n"
        f"<b>Количество шортсов</b>: {shorts_number_text}\n"
        f"<b>Сетка</b>: {layout_map.get(config.get('layout'), 'Не выбрано')}\n"
        f"<b>Brainrot видео</b>: {video_map.get(config.get('bottom_video'), 'Нет')}\n"
        f"<b>Тип субтитров</b>: {sub_type_map.get(config.get('subtitles_type'), 'Не выбрано')}\n"
    )
    if config.get('subtitles_type') != 'no_subtitles':
        settings_text += f"<b>Цвет субтитров</b>: {sub_style_map.get(config.get('subtitle_style'), 'Не выбрано')}\n"

    return settings_text
