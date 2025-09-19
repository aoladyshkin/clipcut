import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import ContextTypes, ConversationHandler
from database import get_user, update_user_balance, add_to_user_balance
import os
import asyncio
from bot_logic import main as process_video
from states import (
    GET_URL,
    GET_SUBTITLE_STYLE,
    GET_BOTTOM_VIDEO,
    GET_LAYOUT,
    GET_SUBTITLES_TYPE,
    GET_CAPITALIZE,
    CONFIRM_CONFIG,
    GET_AI_TRANSCRIPTION,
    GET_SHORTS_NUMBER,
    GET_TOPUP_METHOD,
    GET_TOPUP_PACKAGE
)

logger = logging.getLogger(__name__)

async def get_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет URL и запрашивает первую настройку."""
    url = update.message.text
    if "youtube.com/" not in url and "youtu.be/" not in url:
        await update.message.reply_text("Пожалуйста, пришлите корректную ссылку на YouTube видео.")
        return GET_URL

    context.user_data['url'] = url
    logger.info(f"Пользователь {update.effective_user.id} предоставил URL: {url}")

    keyboard = [
        [
            InlineKeyboardButton("Скачать с YouTube", callback_data='youtube'),
            InlineKeyboardButton("С помощью AI (дольше, но точнее)", callback_data='ai'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Как получить транскрипцию видео?", reply_markup=reply_markup)
    return GET_AI_TRANSCRIPTION

async def get_ai_transcription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет выбор транскрипции и запрашивает количество шортсов."""
    query = update.callback_query
    await query.answer()
    context.user_data['config']['force_ai_transcription'] = query.data == 'ai'
    logger.info(f"Config for {query.from_user.id}: force_ai_transcription = {context.user_data['config']['force_ai_transcription']}")

    keyboard = [
        [InlineKeyboardButton("Авто", callback_data='auto')],
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_get_ai_transcription')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        "Сколько шортсов мне нужно сделать? Отправьте число или нажмите \"Авто\"",
        reply_markup=reply_markup
    )
    return GET_SHORTS_NUMBER


async def get_shorts_number_auto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет 'Авто' для количества шортсов и запрашивает сетку."""
    query = update.callback_query
    await query.answer()
    context.user_data['config']['shorts_number'] = 'auto'
    logger.info(f"Config for {query.from_user.id}: shorts_number = 'auto'")

    keyboard = [
        [
            InlineKeyboardButton("Осн. видео (верх) + brainrot снизу", callback_data='top_bottom'),
            InlineKeyboardButton("Только основное видео", callback_data='main_only'),
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_shorts_number')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Выберите сетку шортса:", reply_markup=reply_markup)
    return GET_LAYOUT


async def get_shorts_number_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет число шортсов и запрашивает сетку."""
    try:
        number = int(update.message.text)
        balance = context.user_data.get('balance', 0)

        if 'error_message_id' in context.user_data:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=context.user_data['error_message_id'])
            del context.user_data['error_message_id']

        if number <= 0:
            msg = await update.message.reply_text("Пожалуйста, введите положительное число.")
            context.user_data['error_message_id'] = msg.message_id
            return GET_SHORTS_NUMBER
        
        if number > balance:
            msg = await update.message.reply_text(f"У вас на балансе {balance} шортсов. Пожалуйста, введите число не больше {balance}.")
            context.user_data['error_message_id'] = msg.message_id
            return GET_SHORTS_NUMBER

        context.user_data['config']['shorts_number'] = number
        logger.info(f"Config for {update.effective_user.id}: shorts_number = {number}")

        keyboard = [
            [
                InlineKeyboardButton("Осн. видео (верх) + brainrot снизу", callback_data='top_bottom'),
                InlineKeyboardButton("Только основное видео", callback_data='main_only'),
            ],
            [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_shorts_number')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Выберите сетку шортса:", reply_markup=reply_markup)
        return GET_LAYOUT

    except ValueError:
        msg = await update.message.reply_text("Пожалуйста, введите целое число или нажмите кнопку 'Авто'.")
        context.user_data['error_message_id'] = msg.message_id
        return GET_SHORTS_NUMBER

async def get_subtitle_style(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет стиль субтитров и запрашивает капитализацию."""
    query = update.callback_query
    await query.answer()
    context.user_data['config']['subtitle_style'] = query.data
    logger.info(f"Config for {query.from_user.id}: subtitle_style = {query.data}")

    keyboard = [
        [
            InlineKeyboardButton("Да", callback_data='true'),
            InlineKeyboardButton("Нет", callback_data='false'),
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_subtitles_type')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text="Начинать предложения в субтитрах с заглавной буквы?", reply_markup=reply_markup)
    return GET_CAPITALIZE

async def get_bottom_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет фоновое видео и запрашивает тип субтитров."""
    query = update.callback_query
    await query.answer()
    choice = query.data if query.data != 'none' else None
    context.user_data['config']['bottom_video'] = choice
    logger.info(f"Config for {query.from_user.id}: bottom_video = {choice}")

    keyboard = [
        [
            InlineKeyboardButton("По одному слову", callback_data='word-by-word'),
            InlineKeyboardButton("По фразе", callback_data='phrases'),
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_layout')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text="Выберите, как показывать субтитры:", reply_markup=reply_markup)
    return GET_SUBTITLES_TYPE

async def get_layout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет расположение и запрашивает фоновое видео (или пропускает, если main_only)."""
    query = update.callback_query
    await query.answer()
    layout_choice = query.data
    context.user_data['config']['layout'] = layout_choice
    logger.info(f"Config for {query.from_user.id}: layout = {layout_choice}")

    if layout_choice == 'main_only':
        context.user_data['config']['bottom_video'] = None # Explicitly set to None
        logger.info(f"Layout for {query.from_user.id} is main_only, skipping bottom video selection.")
        
        keyboard = [
            [
                InlineKeyboardButton("По одному слову", callback_data='word-by-word'),
                InlineKeyboardButton("По фразе", callback_data='phrases'),
            ],
            [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_layout')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text="Выберите, как показывать субтитры:", reply_markup=reply_markup)
        return GET_SUBTITLES_TYPE
    else:
        keyboard = [
            [
                InlineKeyboardButton("GTA", callback_data='gta'),
                InlineKeyboardButton("Minecraft", callback_data='minecraft'),
            ],
            [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_layout')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text="Выберите brainrot видео:", reply_markup=reply_markup)
        return GET_BOTTOM_VIDEO

async def get_subtitles_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет тип субтитров и запрашивает стиль субтитров."""
    query = update.callback_query
    await query.answer()
    context.user_data['config']['subtitles_type'] = query.data
    logger.info(f"Config for {query.from_user.id}: subtitles_type = {query.data}")

    keyboard = [
        [InlineKeyboardButton("Белый", callback_data='white'), InlineKeyboardButton("Желтый", callback_data='yellow')],
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_subtitles_type')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text="Выберите цвет субтитров:", reply_markup=reply_markup)
    return GET_SUBTITLE_STYLE

def format_config(config, balance=None):
    layout_map = {'top_bottom': 'Осн. видео + brainrot', 'main_only': 'Только основное видео'}
    video_map = {'gta': 'GTA', 'minecraft': 'Minecraft', None: 'Нет'}
    sub_type_map = {'word-by-word': 'По одному слову', 'phrases': 'По фразе'}
    sub_style_map = {'white': 'Белый', 'yellow': 'Желтый'}
    capitalize_map = {True: 'Да', False: 'Нет'}
    transcription_map = {True: 'С помощью AI', False: 'Скачать с YouTube'}
    shorts_number = config.get('shorts_number', 'Авто')
    if shorts_number != 'auto':
        shorts_number_text = str(shorts_number)
    else:
        shorts_number_text = 'Авто'

    balance_text = f"<b>Ваш баланс</b>: {balance} шортсов\n" if balance is not None else ""

    settings_text = (
        f"{balance_text}"
        f"<b>Количество шортсов</b>: {shorts_number_text}\n"
        f"<b>Способ транскрипции</b>: {transcription_map.get(config.get('force_ai_transcription'), 'Не выбрано')}\n"
        f"<b>Сетка</b>: {layout_map.get(config.get('layout'), 'Не выбрано')}\n"
        f"<b>Brainrot видео</b>: {video_map.get(config.get('bottom_video'), 'Нет')}\n"
        f"<b>Тип субтитров</b>: {sub_type_map.get(config.get('subtitles_type'), 'Не выбрано')}\n"
        f"<b>Цвет субтитров</b>: {sub_style_map.get(config.get('subtitle_style'), 'Не выбрано')}\n"
        f"<b>Заглавные буквы в начале предложений</b>: {capitalize_map.get(config.get('capitalize_sentences'), 'Не выбрано')}"
    )
    return settings_text

async def get_capitalize(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет капитализацию и показывает экран подтверждения."""
    query = update.callback_query
    await query.answer()
    context.user_data['config']['capitalize_sentences'] = query.data == 'true'
    logger.info(f"Config for {query.from_user.id}: capitalize_sentences = {context.user_data['config']['capitalize_sentences']}")

    balance = context.user_data.get('balance')
    settings_text = format_config(context.user_data['config'], balance)

    keyboard = [
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data='confirm'),
            InlineKeyboardButton("❌ Отклонить", callback_data='cancel'),
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_subtitle_style')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text=f"Подтвердите настройки:\n\n{settings_text}",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

    return CONFIRM_CONFIG

async def confirm_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Добавляет задачу в очередь после подтверждения."""
    query = update.callback_query
    await query.answer()

    balance = context.user_data.get('balance', 0)
    shorts_number = context.user_data.get('config', {}).get('shorts_number', 'auto')

    if isinstance(shorts_number, int):
        if balance < shorts_number:
            await query.edit_message_text(
                f"На вашем балансе ({balance}) недостаточно шортсов для генерации {shorts_number} видео. Пожалуйста, пополните баланс или выберите меньшее количество."
            )
            return ConversationHandler.END
    elif shorts_number == 'auto':
        # В режиме "авто" мы не знаем точное количество. 
        # Можно либо списать максимум, либо проверять по факту.
        # Пока что просто пропускаем, если баланс > 0, что уже проверено в start.
        pass

    processing_queue = context.bot_data['processing_queue']
    task_data = {
        'chat_id': query.message.chat.id,
        'user_data': context.user_data.copy()
    }
    await processing_queue.put(task_data)
    
    logger.info(f"Задача для чата {query.message.chat.id} добавлена в очередь. Задач в очереди: {processing_queue.qsize()}")

    await query.edit_message_text(text=f"Ваш запрос добавлен в очередь (вы #{processing_queue.qsize()} в очереди). Вы получите уведомление, когда обработка начнется.")

    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отменяет текущую конфигурацию и возвращает к началу."""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data['config'] = {}
    await query.edit_message_text(
        "Пришли мне ссылку на YouTube видео, и я сделаю из него короткие виральные ролики."
    )
    return GET_URL

async def back_to_get_ai_transcription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [
        [
            InlineKeyboardButton("Скачать с YouTube", callback_data='youtube'),
            InlineKeyboardButton("С помощью AI (дольше, но точнее)", callback_data='ai'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Как получить транскрипцию видео?", reply_markup=reply_markup)
    return GET_AI_TRANSCRIPTION

async def back_to_shorts_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("Авто", callback_data='auto')],
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_get_ai_transcription')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        "Сколько шортсов мне нужно сделать? Отправьте число или нажмите \"Авто\"",
        reply_markup=reply_markup
    )
    return GET_SHORTS_NUMBER

async def back_to_layout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [
        [
            InlineKeyboardButton("Осн. видео (верх) + brainrot снизу", callback_data='top_bottom'),
            InlineKeyboardButton("Только основное видео", callback_data='main_only'),
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_shorts_number')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Выберите сетку шортса:", reply_markup=reply_markup)
    return GET_LAYOUT

async def back_to_bottom_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if context.user_data.get('config', {}).get('layout') == 'main_only':
        return await back_to_layout(update, context)
    keyboard = [
        [
            InlineKeyboardButton("GTA", callback_data='gta'),
            InlineKeyboardButton("Minecraft", callback_data='minecraft'),
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_layout')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text="Выберите brainrot видео:", reply_markup=reply_markup)
    return GET_BOTTOM_VIDEO

async def back_to_subtitles_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [
        [
            InlineKeyboardButton("По одному слову", callback_data='word-by-word'),
            InlineKeyboardButton("По фразе", callback_data='phrases'),
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_bottom_video')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text="Выберите, как показывать субтитры:", reply_markup=reply_markup)
    return GET_SUBTITLES_TYPE

async def back_to_subtitle_style(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("Белый", callback_data='white'), InlineKeyboardButton("Желтый", callback_data='yellow')],
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_subtitles_type')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text="Выберите цвет субтитров:", reply_markup=reply_markup)
    return GET_SUBTITLE_STYLE

async def back_to_get_capitalize(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [
        [
            InlineKeyboardButton("Да", callback_data='true'),
            InlineKeyboardButton("Нет", callback_data='false'),
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_subtitle_style')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text="Начинать предложения в субтитрах с заглавной буквы?", reply_markup=reply_markup)
    return GET_CAPITALIZE

async def back_to_topup_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Goes back to the top-up method selection."""
    query = update.callback_query
    await query.answer()
    keyboard = [
        [
            InlineKeyboardButton("Telegram Stars", callback_data='topup_stars'),
            InlineKeyboardButton("CryptoBot", callback_data='topup_crypto'),
        ],
        [InlineKeyboardButton("❌ Отмена", callback_data='cancel_topup')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Выберите способ пополнения:", reply_markup=reply_markup)
    return GET_TOPUP_METHOD

async def topup_stars(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Shows the available packages for Telegram Stars top-up."""
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("10 шортсов - 100 ⭐️", callback_data='topup_10_100')],
        [InlineKeyboardButton("25 шортсов - 225 ⭐️", callback_data='topup_25_225')],
        [InlineKeyboardButton("50 шортсов - 400 ⭐️", callback_data='topup_50_400')],
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_topup_method')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Выберите пакет для пополнения через Telegram Stars:", reply_markup=reply_markup)
    return GET_TOPUP_PACKAGE

async def send_invoice_for_stars(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Sends an invoice for the selected package."""
    query = update.callback_query
    await query.answer()
    
    package = query.data.split('_')
    shorts_amount = int(package[1])
    stars_amount = int(package[2])
    
    chat_id = update.effective_chat.id
    title = f"Пополнение баланса на {shorts_amount} шортсов"
    description = f"Пакет '{shorts_amount} шортсов' для генерации видео."
    payload = f"topup-{chat_id}-{shorts_amount}-{stars_amount}"
    currency = "XTR"
    prices = [LabeledPrice(f"{shorts_amount} шортсов", stars_amount)]

    await context.bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description=description,
        payload=payload,
        provider_token=None,  # Not needed for Telegram Stars
        currency=currency,
        prices=prices
    )
    return ConversationHandler.END

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Answers the PreCheckoutQuery."""
    query = update.pre_checkout_query
    if query.invoice_payload.startswith('topup-'):
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Что-то пошло не так...")

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirms the successful payment."""
    payment_info = update.message.successful_payment
    payload_parts = payment_info.invoice_payload.split('-')
    user_id = int(payload_parts[1])
    shorts_amount = int(payload_parts[2])

    add_to_user_balance(user_id, shorts_amount)
    _, new_balance, _ = get_user(user_id)

    await context.bot.send_message(
        chat_id=user_id,
        text=f"Оплата прошла успешно! Ваш баланс пополнен на {shorts_amount} шортсов. \nНовый баланс: {new_balance} шортсов."
    )

async def topup_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the CryptoBot top-up option."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Пополнение через CryptoBot пока не реализовано.")
    return ConversationHandler.END

async def cancel_topup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the top-up process."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Пополнение отменено.")
    return ConversationHandler.END