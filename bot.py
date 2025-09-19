import os
import logging
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, Bot, BotCommandScopeDefault
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler, CallbackQueryHandler, ContextTypes
import asyncio
from typing import Dict

# Импортируем основную функцию из вашего скрипта
from bot_logic import main as process_video
from database import get_user, update_user_balance

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()

# Состояния для диалога
(GET_URL, GET_SUBTITLE_STYLE, GET_BOTTOM_VIDEO, 
GET_LAYOUT, GET_SUBTITLES_TYPE, GET_CAPITALIZE, CONFIRM_CONFIG, GET_AI_TRANSCRIPTION, GET_SHORTS_NUMBER, GET_TOPUP_METHOD) = range(10)

async def processing_worker(queue: asyncio.Queue, bot: Bot):
    """Воркер, который обрабатывает видео из очереди."""
    while True:
        task_data = await queue.get()
        chat_id = task_data['chat_id']
        user_data = task_data['user_data']
        
        try:
            logger.info(f"Начинаю обработку задачи для чата {chat_id}")
            await run_processing(chat_id, user_data, bot)
        except Exception as e:
            logger.error(f"Ошибка в воркере для чата {chat_id}: {e}", exc_info=True)
            try:
                await bot.send_message(chat_id, f"Произошла критическая ошибка во время обработки вашего видео: {e}")
            except Exception as send_e:
                logger.error(f"Не удалось отправить сообщение об ошибке в чат {chat_id}: {send_e}")
        finally:
            queue.task_done()
            logger.info(f"Завершена обработка задачи для чата {chat_id}. Задач в очереди: {queue.qsize()}")


async def run_processing(chat_id: int, user_data: Dict, bot: Bot):
    """Асинхронно запускает обработку видео и отправляет результат."""
    status_message = await bot.send_message(chat_id, "Ваш запрос взят в работу. Начинаю скачивание и обработку видео... Это может занять некоторое время.")

    main_loop = asyncio.get_running_loop()

    async def send_status_update_async(status_text: str):
        nonlocal status_message
        # Просто отправляем новое сообщение, так как редактирование может быть сложным
        await bot.send_message(
            chat_id=chat_id,
            text=f"{status_text}"
        )

    def send_status_update(status_text: str):
        asyncio.run_coroutine_threadsafe(send_status_update_async(status_text), main_loop)

    async def send_video_async(file_path, hook, start, end):
        caption = f"Hook: {hook}\n\nТаймкод начала: {start}\n\nТаймкод конца: {end}"
        try:
            with open(file_path, 'rb') as video_file:
                await bot.send_video(
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
            logger.error(f"Ошибка при отправке видео {file_path} в чат {chat_id}: {e}")
            await bot.send_message(chat_id, f"Не удалось отправить видео: {file_path}\n\nОшибка: {e}")

    def send_video_callback(file_path, hook, start, end):
        return asyncio.run_coroutine_threadsafe(send_video_async(file_path, hook, start, end), main_loop)

    try:
        delete_output = os.environ.get("DELETE_OUTPUT_AFTER_SENDING", "false").lower() == "true"
        
        shorts_generated_count = await asyncio.to_thread(
            process_video,
            user_data['url'],
            user_data['config'],
            send_status_update,
            send_video_callback,
            delete_output
        )

        if shorts_generated_count > 0:
            update_user_balance(chat_id, shorts_generated_count)
            logger.info(f"Баланс пользователя {chat_id} обновлен. Списано {shorts_generated_count} шортсов.")
            _, new_balance, _ = get_user(chat_id)
            await bot.send_message(
                chat_id=chat_id,
                text=f"Обработка завершена! Ваш новый баланс: {new_balance} шортсов."
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="Обработка завершена, но не было создано ни одного шортса. Ваш баланс не изменился."
            )


    except Exception as e:
        logger.error(f"Ошибка при обработке видео для чата {chat_id}: {e}", exc_info=True)
        await bot.send_message(
            chat_id=chat_id,
            text=f"Произошла критическая ошибка во время обработки видео: {e}"
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало диалога, запрашивает URL."""
    user_id = update.effective_user.id
    user = get_user(user_id)
    _, balance, _ = user

    if balance <= 0:
        await update.message.reply_text("У вас закончились шортсы. Пожалуйста, пополните баланс.")
        return ConversationHandler.END

    context.user_data.clear()
    context.user_data['config'] = {}
    context.user_data['balance'] = balance
    
    await update.message.reply_text(
        f"Привет! У вас на балансе {balance} шортсов. \nПришли мне ссылку на YouTube видео, и я сделаю из него короткие виральные ролики."
    )
    return GET_URL

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

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет сообщение с помощью и списком команд."""
    help_text = (
        "Этот бот создает короткие вирусные видео из YouTube роликов.\n\n"
        "Доступные команды:\n"
        "/start - Начать работу с ботом\n"
        "/help - Показать это сообщение\n"
        "/balance - Показать текущий баланс"
    )
    await update.message.reply_text(help_text)

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет текущий баланс пользователя."""
    user_id = update.effective_user.id
    _, balance, _ = get_user(user_id)
    await update.message.reply_text(f"Ваш текущий баланс: {balance} шортсов.")

async def set_commands(application: Application):
    commands = [
        BotCommand(command="start", description="Начать работу"),
        BotCommand(command="help", description="Помощь и описание"),
        BotCommand(command="balance", description="Показать баланс"),
        BotCommand(command="topup", description="Пополнить баланс"),
    ]
    await application.bot.delete_my_commands(scope=BotCommandScopeDefault())
    await application.bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    logger.info("Команды бота успешно установлены.")

async def post_init_hook(application: Application):
    """Выполняется после инициализации приложения для настройки фоновых задач."""
    await set_commands(application)

    # Создаем и сохраняем очередь в bot_data
    processing_queue = asyncio.Queue()
    application.bot_data['processing_queue'] = processing_queue

    # Запускаем воркеры в зависимости от MAX_CONCURRENT_TASKS
    try:
        max_concurrent_tasks = int(os.environ.get("MAX_CONCURRENT_TASKS", "1"))
    except ValueError:
        max_concurrent_tasks = 1
        logger.warning("Неверное значение для MAX_CONCURRENT_TASKS, используется значение по умолчанию: 1")

    for i in range(max_concurrent_tasks):
        asyncio.create_task(processing_worker(processing_queue, application.bot))
        logger.info(f"Запущен воркер #{i + 1}")

    logger.info(f"Очередь обработки и {max_concurrent_tasks} воркер(а/ов) успешно запущены.")

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

async def topup_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the top-up process."""
    keyboard = [
        [
            InlineKeyboardButton("Telegram Stars", callback_data='topup_stars'),
            InlineKeyboardButton("CryptoBot", callback_data='topup_crypto'),
        ],
        [InlineKeyboardButton("❌ Отмена", callback_data='cancel_topup')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите способ пополнения:", reply_markup=reply_markup)
    return GET_TOPUP_METHOD

async def topup_stars(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the Telegram Stars top-up option."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Пополнение через Telegram Stars пока не реализовано.")
    return ConversationHandler.END

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

def main():
    """Основная функция для запуска бота."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.critical("Ошибка: Токен бота не найден. Установите переменную окружения TELEGRAM_BOT_TOKEN.")
        return

    application = Application.builder().token(token).post_init(post_init_hook).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start), CommandHandler("topup", topup_start)],
        states={
            GET_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_url)],
            GET_AI_TRANSCRIPTION: [
                CallbackQueryHandler(get_ai_transcription, pattern='^(youtube|ai)$'),
            ],
            GET_SHORTS_NUMBER: [
                CallbackQueryHandler(get_shorts_number_auto, pattern='^auto$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_shorts_number_manual),
                CallbackQueryHandler(back_to_get_ai_transcription, pattern='^back_to_get_ai_transcription$')
            ],
            GET_LAYOUT: [
                CallbackQueryHandler(get_layout, pattern='^(top_bottom|main_only)$'),
                CallbackQueryHandler(back_to_shorts_number, pattern='^back_to_shorts_number$')
            ],
            GET_BOTTOM_VIDEO: [
                CallbackQueryHandler(get_bottom_video, pattern='^(gta|minecraft|none)$'),
                CallbackQueryHandler(back_to_layout, pattern='^back_to_layout$')
            ],
            GET_SUBTITLES_TYPE: [
                CallbackQueryHandler(get_subtitles_type, pattern='^(word-by-word|phrases)$'),
                CallbackQueryHandler(back_to_bottom_video, pattern='^back_to_bottom_video$'),
                CallbackQueryHandler(back_to_layout, pattern='^back_to_layout$')
            ],
            GET_SUBTITLE_STYLE: [
                CallbackQueryHandler(get_subtitle_style, pattern='^(white|yellow)$'),
                CallbackQueryHandler(back_to_subtitles_type, pattern='^back_to_subtitles_type$')
            ],
            GET_CAPITALIZE: [
                CallbackQueryHandler(get_capitalize, pattern='^(true|false)$'),
                CallbackQueryHandler(back_to_subtitle_style, pattern='^back_to_subtitle_style$')
            ],
            CONFIRM_CONFIG: [
                CallbackQueryHandler(confirm_config, pattern='^confirm$'),
                CallbackQueryHandler(cancel_conversation, pattern='^cancel$'),
                CallbackQueryHandler(back_to_get_capitalize, pattern='^back_to_get_capitalize$')
            ],
            GET_TOPUP_METHOD: [
                CallbackQueryHandler(topup_stars, pattern='^topup_stars$'),
                CallbackQueryHandler(topup_crypto, pattern='^topup_crypto$'),
                CallbackQueryHandler(cancel_topup, pattern='^cancel_topup$')
            ],
        },
        fallbacks=[CommandHandler("start", start)],
        conversation_timeout=600, # 10 минут на диалог
        allow_reentry=True
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("balance", balance_command))

    logger.info("Бот запущен и готов к работе...")
    application.run_polling()

if __name__ == "__main__":
    main()
