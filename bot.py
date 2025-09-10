import os
import logging
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler, CallbackQueryHandler, ContextTypes
import asyncio
import threading

# Импортируем основную функцию из вашего скрипта
from bot_logic import main as process_video

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()

# Состояния для диалога
(GET_URL, GET_SUBTITLE_STYLE, GET_BOTTOM_VIDEO, 
GET_LAYOUT, GET_SUBTITLES_TYPE, GET_CAPITALIZE, CONFIRM_CONFIG) = range(7)

def run_blocking_task(target, args):
    """Запускает блокирующую задачу в отдельном потоке."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(target(*args))
    loop.close()
    return result

async def run_processing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Асинхронно запускает обработку видео и отправляет результат."""
    chat_id = update.message.chat.id
    status_message = await context.bot.send_message(chat_id, "Начинаю скачивание и обработку видео... Это может занять некоторое время.")

    main_loop = asyncio.get_running_loop()

    async def send_status_update_async(status_text: str):
        nonlocal status_message
        status_message = await context.bot.send_message(
            chat_id=chat_id,
            text=f"{status_text}"
        )

    # Synchronous wrapper for the async status update
    def send_status_update(status_text: str):
        asyncio.run_coroutine_threadsafe(send_status_update_async(status_text), main_loop)

    async def send_video_async(file_path, hook, start, end):
        caption = f"Hook: {hook}\n\nТаймкод начала: {start}\n\nТаймкод конца: {end}"
        try:
            with open(file_path, 'rb') as video_file:
                await context.bot.send_video(
                    chat_id=chat_id, 
                    video=video_file, 
                    caption=caption, 
                    width=720, 
                    height=1280, 
                    supports_streaming=True,
                    read_timeout=600,
                    write_timeout=600
                )
        except Exception as e:
            logger.error(f"Ошибка при отправке видео {file_path}: {e}")
            await context.bot.send_message(chat_id, f"Не удалось отправить видео: {file_path}\n\nОшибка: {e}")

    # Synchronous wrapper for the async video sending
    def send_video_callback(file_path, hook, start, end):
        return asyncio.run_coroutine_threadsafe(send_video_async(file_path, hook, start, end), main_loop)

    try:
        # Запускаем блокирующую функцию в отдельном потоке, чтобы не блокировать бота
        # process_video no longer returns results, it sends them via callback
        delete_output = os.environ.get("DELETE_OUTPUT_AFTER_SENDING", "false").lower() == "true"
        await asyncio.to_thread(
            process_video,
            context.user_data['url'],
            context.user_data['config'],
            send_status_update, # Pass the status callback
            send_video_callback, # Pass the video sending callback
            delete_output
        )

        # No need to check 'results' or iterate through them here
        await context.bot.send_message(
            chat_id=chat_id,
            text="Обработка завершена! Все видео отправлены."
        )

    except Exception as e:
        logger.error(f"Ошибка при обработке видео: {e}", exc_info=True)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_message.message_id,
            text=f"Произошла критическая ошибка во время обработки видео: {e}"
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало диалога, запрашивает URL."""
    context.user_data.clear()
    context.user_data['config'] = {}
    await update.message.reply_text(
        "Привет! Пришли мне ссылку на YouTube видео, и я сделаю из него короткие виральные ролики."
    )
    return GET_URL

async def get_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет URL и запрашивает первую настройку."""
    url = update.message.text
    if "youtube.com/" not in url and "youtu.be/" not in url:
        await update.message.reply_text("Пожалуйста, пришлите корректную ссылку на YouTube видео.")
        return GET_URL

    context.user_data['url'] = url
    logger.info(f"Пользователь предоставил URL: {url}")

    keyboard = [
        [
            InlineKeyboardButton("Осн. видео (верх) + brainrot снизу", callback_data='top_bottom'),
            InlineKeyboardButton("Только основное видео", callback_data='main_only'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите сетку шортса:", reply_markup=reply_markup)
    return GET_LAYOUT

async def get_subtitle_style(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет стиль субтитров и запрашивает капитализацию."""
    query = update.callback_query
    await query.answer()
    context.user_data['config']['subtitle_style'] = query.data
    logger.info(f"Config: subtitle_style = {query.data}")

    keyboard = [
        [
            InlineKeyboardButton("Да", callback_data='true'),
            InlineKeyboardButton("Нет", callback_data='false'),
        ]
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
    logger.info(f"Config: bottom_video = {choice}")

    keyboard = [
        [
            InlineKeyboardButton("По одному слову", callback_data='word-by-word'),
            InlineKeyboardButton("По фразе", callback_data='phrases'),
        ]
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
    logger.info(f"Config: layout = {layout_choice}")

    if layout_choice == 'main_only':
        context.user_data['config']['bottom_video'] = None # Explicitly set to None
        logger.info("Layout is main_only, skipping bottom video selection.")
        
        # Directly ask for subtitles_type
        keyboard = [
            [
                InlineKeyboardButton("По одному слову", callback_data='word-by-word'),
                InlineKeyboardButton("По фразе", callback_data='phrases'),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text="Выберите, как показывать субтитры:", reply_markup=reply_markup)
        return GET_SUBTITLES_TYPE
    else:
        # Proceed to ask for bottom_video
        keyboard = [
            [
                InlineKeyboardButton("GTA", callback_data='gta'),
                InlineKeyboardButton("Minecraft", callback_data='minecraft'),
            ],
            # [InlineKeyboardButton("Черный фон", callback_data='none')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text="Выберите brainrot видео:", reply_markup=reply_markup)
        return GET_BOTTOM_VIDEO

async def get_subtitles_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет тип субтитров и запрашивает стиль субтитров."""
    query = update.callback_query
    await query.answer()
    context.user_data['config']['subtitles_type'] = query.data
    logger.info(f"Config: subtitles_type = {query.data}")

    keyboard = [
        [InlineKeyboardButton("Белый", callback_data='white'), InlineKeyboardButton("Желтый", callback_data='yellow')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text="Выберите цвет субтитров:", reply_markup=reply_markup)
    return GET_SUBTITLE_STYLE

def format_config(config):
    layout_map = {'top_bottom': 'Осн. видео + brainrot', 'main_only': 'Только основное видео'}
    video_map = {'gta': 'GTA', 'minecraft': 'Minecraft', None: 'Нет'}
    sub_type_map = {'word-by-word': 'По одному слову', 'phrases': 'По фразе'}
    sub_style_map = {'white': 'Белый', 'yellow': 'Желтый'}
    capitalize_map = {True: 'Да', False: 'Нет'}

    settings_text = (
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
    logger.info(f"Config: capitalize_sentences = {context.user_data['config']['capitalize_sentences']}")

    settings_text = format_config(context.user_data['config'])

    keyboard = [
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data='confirm'),
            InlineKeyboardButton("❌ Отклонить", callback_data='cancel'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text=f"Подтвердите настройки:\n\n{settings_text}",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

    return CONFIRM_CONFIG

async def confirm_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запускает обработку после подтверждения."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="Конфигурация подтверждена. Начинаю обработку...")

    # Запускаем длительную задачу в фоновом режиме
    asyncio.create_task(run_processing(query, context))

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

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отменяет и завершает диалог."""
    await update.message.reply_text("Действие отменено.")
    context.user_data.clear()
    return ConversationHandler.END

async def set_commands(application: Application):
    commands = [
        BotCommand(command="start", description="Начать работу"),
        BotCommand(command="help", description="Помощь и описание"),
        # BotCommand(command="settings", description="Настройки"),
    ]
    await application.bot.set_my_commands(commands)

def main():
    """Основная функция для запуска бота."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Ошибка: Токен бота не найден. Установите переменную окружения TELEGRAM_BOT_TOKEN.")
        return

    application = Application.builder().token(token).post_init(set_commands).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            GET_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_url)],
            GET_SUBTITLE_STYLE: [CallbackQueryHandler(get_subtitle_style, pattern='^(white|yellow)')],
            GET_BOTTOM_VIDEO: [CallbackQueryHandler(get_bottom_video, pattern='^(gta|minecraft|none)')],
            GET_LAYOUT: [CallbackQueryHandler(get_layout, pattern='^(top_bottom|main_only)')],
            GET_SUBTITLES_TYPE: [CallbackQueryHandler(get_subtitles_type, pattern='^(word-by-word|phrases)')],
            GET_CAPITALIZE: [CallbackQueryHandler(get_capitalize, pattern='^(true|false)')],
            CONFIRM_CONFIG: [
                CallbackQueryHandler(confirm_config, pattern='^confirm'),
                CallbackQueryHandler(cancel_conversation, pattern='^cancel'),
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=600 # 10 минут на диалог
    )

    application.add_handler(conv_handler)

    print("Бот запущен и готов к работе...")
    application.run_polling()

if __name__ == "__main__":
    main()
