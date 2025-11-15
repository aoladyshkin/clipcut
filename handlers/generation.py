import logging
import uuid
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from database import get_user, get_user_tasks_from_queue, add_task_to_queue, get_total_queue_length
from analytics import log_event
from states import (
    GET_URL, GET_SHORTS_NUMBER, GET_LAYOUT, GET_SUBTITLES_TYPE, GET_SUBTITLE_STYLE, 
    GET_BANNER, CONFIRM_CONFIG, PROCESSING, GET_BRAINROT, GET_FACE_TRACKING
)
from localization import get_translation
from processing.download import check_video_availability
from utils import format_config, get_video_platform
from config import (
    CONFIG_EXAMPLES_DIR, ADMIN_USER_IDS, MODERATORS_GROUP_ID,
    ADMIN_USER_TAG, MAX_SHORTS_PER_VIDEO
)

logger = logging.getLogger(__name__)

async def url_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Clears the user context and forwards the update to the get_url handler."""
    context.user_data.clear()
    context.user_data['config'] = {}
    
    # Manually set up the user's language if it's not already there
    user_id = update.effective_user.id
    _, _, _, lang, is_new = get_user(user_id)
    if is_new:
        # This is a fallback, as the user might not have used /start yet
        lang = 'ru' 
    context.user_data['lang'] = lang

    logger.info(f"URL entrypoint triggered for user {user_id}. Context cleared.")
    
    return await get_url(update, context)

async def get_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves the URL and prompts for the number of shorts."""
    user_id = update.effective_user.id
    _, _, _, lang, _ = get_user(user_id)
    context.user_data['lang'] = lang

    url = update.message.text
    generation_id = str(uuid.uuid4())
    context.user_data['generation_id'] = generation_id
    log_event(update.effective_user.id, 'config_video_url_provided', {'url': url, 'generation_id': generation_id})

    platform = get_video_platform(url)
    if not platform:
        await update.message.reply_text(get_translation(lang, "send_correct_youtube_link")) # TODO: Update translation for Twitch
        return GET_URL

    context.user_data['config']['platform'] = platform

    # Send a "checking" message
    checking_message = await update.message.reply_text(get_translation(lang, "checking_video_availability"))

    # Check video availability
    is_available, message, err = check_video_availability(url, lang)

    # Delete the "checking" message
    await checking_message.delete()

    if not is_available:
        await update.message.reply_text(message)
        log_event(
            update.effective_user.id,
            'video_availability_error',
            {'url': url, 'error': err}
        )
        logger.warning(f"Ошибка проверки доступности видео: {err}")
        if err == "not enough disk space" and MODERATORS_GROUP_ID:
            await context.bot.send_message(
                chat_id=MODERATORS_GROUP_ID,
                text=f"{ADMIN_USER_TAG} Video processing failed due to insufficient disk space.\n\nError: {err}\nURL: {url}"
            )
        return GET_URL

    context.user_data['url'] = url
    logger.info(f"User {update.effective_user.id} provided URL: {url} (platform: {platform})")

    keyboard = [
        [
            InlineKeyboardButton(get_translation(lang, "auto_button"), callback_data='auto')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message = await update.message.reply_text(
        get_translation(lang, "how_many_shorts_prompt"),
        reply_markup=reply_markup
    )
    context.user_data['shorts_number_message_id'] = message.message_id
    return GET_SHORTS_NUMBER


async def get_shorts_number_auto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves 'Auto' for the number of shorts and prompts for the layout."""
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get('lang', 'en')
    context.user_data['config']['shorts_number'] = 'auto'
    generation_id = context.user_data.get('generation_id')
    log_event(query.from_user.id, 'config_step_shorts_number_selected', {'choice': 'auto', 'generation_id': generation_id})
    logger.info(f"Config for {query.from_user.id}: shorts_number = 'auto'")

    keyboard = [
        [
            InlineKeyboardButton("1:1", callback_data='1_1'),
            InlineKeyboardButton("16:9", callback_data='16_9'),
            InlineKeyboardButton("9:16", callback_data='9_16')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.delete()
    await context.bot.send_photo(
        chat_id=query.message.chat_id,
        photo=open(CONFIG_EXAMPLES_DIR / 'layout_examples.png', 'rb'),
        caption=get_translation(lang, "choose_layout_prompt"),
        reply_markup=reply_markup
    )
    return GET_LAYOUT


async def get_shorts_number_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves the number of shorts and prompts for the layout."""
    lang = context.user_data.get('lang', 'en')
    
    # Delete the bot's prompt message
    if 'shorts_number_message_id' in context.user_data:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=context.user_data.pop('shorts_number_message_id'))
        except Exception as e:
            logger.info(f"Could not delete shorts_number_message_id: {e}")

    # Delete the user's message with the number
    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
    except Exception as e:
        logger.info(f"Could not delete user's message: {e}")

    # Clean up previous error messages if any
    if 'error_message_id' in context.user_data:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=context.user_data.pop('error_message_id'))
        except Exception as e:
            logger.info(f"Could not delete error_message_id: {e}")

    async def resend_prompt(context):
        keyboard = [[InlineKeyboardButton(get_translation(lang, "auto_button"), callback_data='auto')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=get_translation(lang, "how_many_shorts_prompt"),
            reply_markup=reply_markup
        )
        context.user_data['shorts_number_message_id'] = message.message_id

    try:
        number = int(update.message.text)
        balance = context.user_data.get('balance', 0)

        if number <= 0:
            msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=get_translation(lang, "enter_positive_number"))
            context.user_data['error_message_id'] = msg.message_id
            await resend_prompt(context)
            return GET_SHORTS_NUMBER

        if number > MAX_SHORTS_PER_VIDEO:
            msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=get_translation(lang, "max_shorts_exceeded").format(max_shorts=MAX_SHORTS_PER_VIDEO))
            context.user_data['error_message_id'] = msg.message_id
            await resend_prompt(context)
            return GET_SHORTS_NUMBER

        context.user_data['config']['shorts_number'] = number
        generation_id = context.user_data.get('generation_id')
        log_event(update.effective_user.id, 'config_step_shorts_number_selected', {'choice': number, 'generation_id': generation_id})
        logger.info(f"Config for {update.effective_user.id}: shorts_number = {number}")

        keyboard = [
            [
                InlineKeyboardButton("1:1", callback_data='1_1'),
                InlineKeyboardButton("16:9", callback_data='16_9'),
                InlineKeyboardButton("9:16", callback_data='9_16')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=open(CONFIG_EXAMPLES_DIR / 'layout_examples.png', 'rb'),
            caption=get_translation(lang, "choose_layout_prompt"),
            reply_markup=reply_markup
        )
        return GET_LAYOUT
    except ValueError:
        msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=get_translation(lang, "enter_integer_or_auto"))
        context.user_data['error_message_id'] = msg.message_id
        await resend_prompt(context)
        return GET_SHORTS_NUMBER

async def get_subtitle_style(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves the subtitle style and shows the confirmation screen."""
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get('lang', 'en')
    context.user_data['config']['subtitle_style'] = query.data
    generation_id = context.user_data.get('generation_id')
    log_event(query.from_user.id, 'config_step_subtitle_style_selected', {'choice': query.data, 'generation_id': generation_id})
    logger.info(f"Config for {query.from_user.id}: subtitle_style = {query.data}")

    # Set default capitalization
    context.user_data['config']['capitalize_sentences'] = False
    logger.info(f"Config for {query.from_user.id}: capitalize_sentences = False (default)")

    return await ask_for_banner(update, context)

async def get_layout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves the layout and prompts for brainrot or face tracking."""
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get('lang', 'en')
    layout_choice = query.data
    context.user_data['layout_choice'] = layout_choice  # Store the raw choice
    generation_id = context.user_data.get('generation_id')
    log_event(query.from_user.id, 'config_step_layout_selected', {'choice': layout_choice, 'generation_id': generation_id})
    logger.info(f"Config for {query.from_user.id}: layout_choice = {layout_choice}")

    await query.message.delete()

    if layout_choice in ['1_1', '16_9']:
        return await ask_for_brainrot(update, context)
    
    elif layout_choice == '9_16':
        context.user_data['config']['layout'] = 'face_track_9_16'
        context.user_data['config']['bottom_video'] = None
        keyboard = [
            [
                InlineKeyboardButton(get_translation(lang, "yes_track_face"), callback_data='track_yes'),
                InlineKeyboardButton(get_translation(lang, "no_track_face"), callback_data='track_no'),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=get_translation(lang, "ask_face_tracking"),
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        return GET_FACE_TRACKING

async def ask_for_brainrot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Asks the user to choose a brainrot video."""
    lang = context.user_data.get('lang', 'en')
    keyboard = [
        [
            InlineKeyboardButton(get_translation(lang, "gta_button"), callback_data='gta'),
            InlineKeyboardButton(get_translation(lang, "minecraft_button"), callback_data='minecraft'),
        ],
        [InlineKeyboardButton(get_translation(lang, "no_brainrot_button"), callback_data='no_brainrot')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_photo(
        chat_id=update.callback_query.message.chat_id,
        photo=open(CONFIG_EXAMPLES_DIR / 'brainrot_examples.png', 'rb'),
        caption=get_translation(lang, "choose_brainrot_video_prompt"),
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    return GET_BRAINROT

async def get_brainrot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves the brainrot choice and determines the next step."""
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get('lang', 'en')
    brainrot_choice = query.data
    layout_choice = context.user_data.get('layout_choice')

    if brainrot_choice == 'no_brainrot':
        context.user_data['config']['bottom_video'] = None
        if layout_choice == '1_1':
            context.user_data['config']['layout'] = 'square_center'
        elif layout_choice == '16_9':
            context.user_data['config']['layout'] = 'full_center'
    else:
        context.user_data['config']['bottom_video'] = brainrot_choice
        if layout_choice == '1_1':
            context.user_data['config']['layout'] = 'square_top_brainrot_bottom'
        elif layout_choice == '16_9':
            context.user_data['config']['layout'] = 'full_top_brainrot_bottom'

    generation_id = context.user_data.get('generation_id')
    log_event(query.from_user.id, 'config_step_brainrot_selected', {'choice': brainrot_choice, 'generation_id': generation_id})
    logger.info(f"Config for {query.from_user.id}: bottom_video = {context.user_data['config']['bottom_video']}")
    logger.info(f"Config for {query.from_user.id}: layout = {context.user_data['config']['layout']}")

    await query.message.delete()

    if layout_choice == '1_1':
        keyboard = [
            [
                InlineKeyboardButton(get_translation(lang, "yes_track_face"), callback_data='track_yes'),
                InlineKeyboardButton(get_translation(lang, "no_track_face"), callback_data='track_no'),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=get_translation(lang, "ask_face_tracking"),
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        return GET_FACE_TRACKING
    elif layout_choice == '16_9':
        keyboard = [
            [
                InlineKeyboardButton(get_translation(lang, "subtitle_type_word"), callback_data='word-by-word'),
                InlineKeyboardButton(get_translation(lang, "subtitle_type_phrase"), callback_data='phrases'),
            ],
            [InlineKeyboardButton(get_translation(lang, "no_subtitles_button"), callback_data='no_subtitles')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=open(CONFIG_EXAMPLES_DIR / 'subs_examples.png', 'rb'),
            caption=get_translation(lang, "choose_subtitle_display_prompt"),
            reply_markup=reply_markup
        )
        return GET_SUBTITLES_TYPE

async def get_face_tracking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves the face tracking choice and determines the next step."""
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get('lang', 'en')
    
    choice = query.data == 'track_yes'
    context.user_data['config']['use_face_tracking'] = choice

    generation_id = context.user_data.get('generation_id')
    log_event(query.from_user.id, 'config_step_face_tracking_selected', {'choice': choice, 'generation_id': generation_id})
    logger.info(f"Config for {query.from_user.id}: use_face_tracking = {choice}")

    await query.message.delete()

    keyboard = [
        [
            InlineKeyboardButton(get_translation(lang, "subtitle_type_word"), callback_data='word-by-word'),
            InlineKeyboardButton(get_translation(lang, "subtitle_type_phrase"), callback_data='phrases'),
        ],
        [InlineKeyboardButton(get_translation(lang, "no_subtitles_button"), callback_data='no_subtitles')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_photo(
        chat_id=query.message.chat_id,
        photo=open(CONFIG_EXAMPLES_DIR / 'subs_examples.png', 'rb'),
        caption=get_translation(lang, "choose_subtitle_display_prompt"),
        reply_markup=reply_markup
    )
    return GET_SUBTITLES_TYPE

async def get_subtitles_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves the subtitle type and prompts for subtitle style or confirmation."""
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get('lang', 'en')
    choice = query.data
    context.user_data['config']['subtitles_type'] = choice
    generation_id = context.user_data.get('generation_id')
    log_event(query.from_user.id, 'config_step_subtitles_type_selected', {'choice': choice, 'generation_id': generation_id})
    logger.info(f"Config for {query.from_user.id}: subtitles_type = {choice}")

    if choice == 'no_subtitles':
        context.user_data['config']['subtitle_style'] = None
        logger.info(f"Config for {query.from_user.id}: subtitle_style = None")
        return await ask_for_banner(update, context)
    else:
        keyboard = [
            [InlineKeyboardButton(get_translation(lang, "white_color_button"), callback_data='white'), InlineKeyboardButton(get_translation(lang, "yellow_color_button"), callback_data='yellow')],
            [InlineKeyboardButton(get_translation(lang, "purple_color_button"), callback_data='purple'), InlineKeyboardButton(get_translation(lang, "green_color_button"), callback_data='green')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.delete()
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=open(CONFIG_EXAMPLES_DIR / 'subs_color_examples.png', 'rb'),
            caption=get_translation(lang, "choose_subtitle_color_prompt"),
            reply_markup=reply_markup
        )
        return GET_SUBTITLE_STYLE

async def ask_for_banner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Asks admin users if they want to add a banner."""
    query = update.callback_query
    user_id = query.from_user.id
    _, balance, _, lang, _ = get_user(user_id)
    context.user_data['lang'] = lang

    if str(user_id) in ADMIN_USER_IDS:
        keyboard = [
            [
                InlineKeyboardButton(get_translation(lang, "yes"), callback_data='banner_yes'),
                InlineKeyboardButton(get_translation(lang, "no"), callback_data='banner_no'),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.delete()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=get_translation(lang, "add_banner_prompt"),
            reply_markup=reply_markup
        )
        return GET_BANNER
    else:
        context.user_data['config']['add_banner'] = False
        settings_text = format_config(context.user_data['config'], balance, lang=lang)
        keyboard = [
            [
                InlineKeyboardButton(get_translation(lang, "confirm_button_emoji"), callback_data='confirm'),
                InlineKeyboardButton(get_translation(lang, "reject_button_emoji"), callback_data='cancel'),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.delete()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=get_translation(lang, "confirm_settings_prompt_html").format(settings_text=settings_text),
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True 
        )
        return CONFIRM_CONFIG

async def get_banner_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves the user's choice about the banner and shows the confirmation screen."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    _, balance, _, lang, _ = get_user(user_id)
    context.user_data['lang'] = lang
    choice = query.data == 'banner_yes'
    context.user_data['config']['add_banner'] = choice
    generation_id = context.user_data.get('generation_id')
    log_event(query.from_user.id, 'config_step_banner_selected', {'choice': choice, 'generation_id': generation_id})
    logger.info(f"Config for {query.from_user.id}: add_banner = {choice}")

    settings_text = format_config(context.user_data['config'], balance, lang=lang)

    keyboard = [
        [
            InlineKeyboardButton(get_translation(lang, "confirm_button_emoji"), callback_data='confirm'),
            InlineKeyboardButton(get_translation(lang, "reject_button_emoji"), callback_data='cancel'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=get_translation(lang, "confirm_settings_prompt_html").format(settings_text=settings_text),
        reply_markup=reply_markup,
        parse_mode="HTML",
        disable_web_page_preview=True 
    )
    return CONFIRM_CONFIG

async def confirm_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Adds a task to the database queue after confirmation."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    _, balance, _, lang, _ = get_user(user_id)

    # First, check for zero balance
    if balance <= 0:
        topup_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(get_translation(lang, "top_up_balance_button"), callback_data='topup_start')]
        ])
        await query.edit_message_text(
            get_translation(lang, "out_of_generations"),
            reply_markup=topup_keyboard
        )
        return ConversationHandler.END

    # Then, check if the user has enough balance to queue another task
    queued_tasks_count = len(get_user_tasks_from_queue(user_id))
    if (queued_tasks_count + 1) > balance:
        await query.edit_message_text(
            get_translation(lang, "insufficient_balance_for_queue").format(
                balance=balance, 
                queued_tasks=queued_tasks_count
            )
        )
        return ConversationHandler.END

    generation_id = context.user_data.get('generation_id')
    
    # Convert user_data to a serializable format (JSON string)
    serializable_user_data = json.dumps(context.user_data.copy())

    # Add task to the database queue
    task_id = add_task_to_queue(
        user_id=user_id,
        chat_id=query.message.chat.id,
        status_message_id=query.message.message_id,
        user_data=serializable_user_data
    )
    
    # Put the task into the in-memory queue for the worker
    processing_queue = context.bot_data['processing_queue']
    task_tuple = (task_id, user_id, query.message.chat.id, serializable_user_data, query.message.message_id)
    await processing_queue.put(task_tuple)
    
    async with context.bot_data['busy_workers_lock']:
        busy_workers = context.bot_data['busy_workers']
    
    total_queue_length = get_total_queue_length()
    
    # Your position in the waiting line
    queue_position = total_queue_length - busy_workers

    event_data = {
        'url': context.user_data['url'],
        'config': context.user_data['config'],
        'generation_id': generation_id,
        'queue_position': queue_position
    }
    log_event(user_id, 'generation_queued', event_data)
    
    logger.info(f"Task {task_id} for user {user_id} added to the DB and in-memory queue at position {queue_position}")

    settings_text = format_config(context.user_data['config'], balance, lang=lang)
    url = context.user_data['url']
    await query.edit_message_text(
        text=get_translation(lang, "request_queued_message").format(queue_position=queue_position, url=url, settings_text=settings_text),
        parse_mode="HTML",
        disable_web_page_preview=True
    )

    return PROCESSING

async def get_bottom_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves the background video and prompts for the subtitle type."""
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get('lang', 'en')
    choice = query.data if query.data != 'none' else None
    context.user_data['config']['bottom_video'] = choice
    generation_id = context.user_data.get('generation_id')
    log_event(query.from_user.id, 'config_step_bottom_video_selected', {'choice': choice, 'generation_id': generation_id})
    logger.info(f"Config for {query.from_user.id}: bottom_video = {choice}")

    keyboard = [
        [
            InlineKeyboardButton(get_translation(lang, "subtitle_type_word"), callback_data='word-by-word'),
            InlineKeyboardButton(get_translation(lang, "subtitle_type_phrase"), callback_data='phrases'),
        ],
        [InlineKeyboardButton(get_translation(lang, "no_subtitles_button"), callback_data='no_subtitles')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.delete()
    await context.bot.send_photo(
        chat_id=query.message.chat_id,
        photo=open(CONFIG_EXAMPLES_DIR / 'subs_examples.png', 'rb'),
        caption=get_translation(lang, "choose_subtitle_display_prompt"),
        reply_markup=reply_markup
    )
    return GET_SUBTITLES_TYPE
