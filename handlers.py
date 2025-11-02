import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import ContextTypes, ConversationHandler
import uuid
import json
from telegram.error import TimedOut, BadRequest
from database import get_user, update_user_balance, add_to_user_balance, add_task_to_queue, get_queue_position
import os
import asyncio
import yookassa
from yookassa import Configuration
from processing.bot_logic import main as process_video
from processing.download import check_video_availability
from utils import format_config
from analytics import log_event
from localization import get_translation
from states import (
    GET_URL,
    GET_SUBTITLE_STYLE,
    GET_BOTTOM_VIDEO,
    GET_LAYOUT,
    GET_FACE_TRACKING,
    GET_SUBTITLES_TYPE,
    CONFIRM_CONFIG,
    GET_SHORTS_NUMBER,
    GET_TOPUP_METHOD,
    GET_TOPUP_PACKAGE,
    GET_CRYPTO_AMOUNT,
    CRYPTO_PAYMENT,
    RATING,
    FEEDBACK,
    PROCESSING,
    GET_FEEDBACK_TEXT,
    GET_TARGETED_BROADCAST_MESSAGE,
    GET_LANGUAGE,
    GET_BANNER,
    GET_YOOKASSA_EMAIL, # Added
    YOOKASSA_PAYMENT
)
from datetime import datetime, timezone
from pricing import DEMO_CONFIG, get_package_prices
from datetime import datetime, timezone
from config import (
    FEEDBACK_GROUP_ID, CONFIG_EXAMPLES_DIR, CRYPTO_BOT_TOKEN, 
    ADMIN_USER_IDS, MODERATORS_GROUP_ID, REWARD_FOR_FEEDBACK,
    YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, ADMIN_USER_TAG, MODERATORS_USER_TAGS,
    MAX_SHORTS_PER_VIDEO
)
from processing.demo import simulate_demo_processing

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

async def handle_dislike_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the dislike button click."""
    query = update.callback_query

    user_id = query.from_user.id
    message_id = query.message.message_id
    chat_id = query.message.chat_id

    _, _, _, lang, _ = get_user(user_id)

    await query.answer(get_translation(lang, "dislike_received"))

    if MODERATORS_GROUP_ID:
        try:
            await context.bot.forward_message(
                chat_id=MODERATORS_GROUP_ID,
                from_chat_id=chat_id,
                message_id=message_id
            )

            moderation_keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(get_translation(lang, "moderation_good"), callback_data=f'moderate_good_{user_id}'),
                    InlineKeyboardButton(get_translation(lang, "moderation_bad"), callback_data=f'moderate_bad_{user_id}')
                ]
            ])

            await context.bot.send_message(
                chat_id=MODERATORS_GROUP_ID,
                text=f"User {user_id} reported a video. {MODERATORS_USER_TAGS}",
                reply_markup=moderation_keyboard
            )

            await query.edit_message_reply_markup(reply_markup=None)
        except Exception as e:
            logger.error(f"Failed to forward dislike message to moderators group: {e}")

async def handle_moderation_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the moderation button click."""
    query = update.callback_query
    await query.answer()

    moderator_id = query.from_user.id
    data = query.data.split('_')
    action = data[1]
    user_id = int(data[2])

    _, _, _, lang, _ = get_user(user_id)

    if action == 'bad':
        add_to_user_balance(user_id, 1)
        await context.bot.send_message(
            chat_id=user_id,
            text=get_translation(lang, "moderation_refund")
        )
        await query.edit_message_text(f"Moderation completed by {moderator_id}. User {user_id} has been refunded.")
    elif action == 'good':
        await context.bot.send_message(
            chat_id=user_id,
            text=get_translation(lang, "moderation_no_problem")
        )
        await query.edit_message_text(f"Moderation completed by {moderator_id}. No issues found.")


async def start_demo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the demo process by loading the demo config."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    _, _, _, lang, _ = get_user(user_id)
    context.user_data['lang'] = lang

    # Import utility function
    from utils import format_config
    import uuid

    # Clear any previous config and set up demo data
    context.user_data.clear()
    # Create a deep copy of the config to avoid modifying the original
    demo_config_copy = {
        "url": DEMO_CONFIG["url"],
        "config": DEMO_CONFIG["config"].copy()
    }
    context.user_data.update(demo_config_copy)
    context.user_data['is_demo'] = True
    context.user_data['generation_id'] = str(uuid.uuid4())
    
    # We don't need to fetch the real balance for the demo
    balance = 0 
    context.user_data['balance'] = balance

    log_event(query.from_user.id, 'demo_started', {'generation_id': context.user_data['generation_id']})
    logger.info(f"User {query.from_user.id} started a demo.")

    settings_text = format_config(context.user_data['config'], balance, is_demo=True, lang=lang) 

    keyboard = [
        [
            InlineKeyboardButton(get_translation(lang, "confirm_button_emoji"), callback_data='confirm_demo'),
            InlineKeyboardButton(get_translation(lang, "reject_button_emoji"), callback_data='cancel'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.message.delete()
    except BadRequest as e:
        logger.warning(f"Could not delete message in start_demo: {e}")
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=get_translation(lang, "demo_mode_started").format(url=context.user_data['url'], settings_text=settings_text),
        reply_markup=reply_markup,
        parse_mode="HTML",
        disable_web_page_preview=True
    )

    return CONFIRM_CONFIG

async def get_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves the URL and prompts for the number of shorts."""
    user_id = update.effective_user.id
    _, balance, _, lang, _ = get_user(user_id)
    context.user_data['balance'] = balance
    context.user_data['lang'] = lang

    if balance <= 0:
        topup_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(get_translation(lang, "top_up_balance_button"), callback_data='topup_start')]
        ])
        await update.message.reply_text(
            get_translation(lang, "out_of_shorts"),
            reply_markup=topup_keyboard
        )
        return ConversationHandler.END

    url = update.message.text
    generation_id = str(uuid.uuid4())
    context.user_data['generation_id'] = generation_id
    log_event(update.effective_user.id, 'config_video_url_provided', {'url': url, 'generation_id': generation_id})

    if "youtube.com/" not in url and "youtu.be/" not in url:
        await update.message.reply_text(get_translation(lang, "send_correct_youtube_link"))
        return GET_URL

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
    logger.info(f"User {update.effective_user.id} provided URL: {url}")

    # Set default transcription method
    context.user_data['config'] = {}
    context.user_data['config']['force_ai_transcription'] = False
    logger.info(f"Config for {update.effective_user.id}: force_ai_transcription = False (default)")

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
            InlineKeyboardButton("1:1", callback_data='square_center'),
            InlineKeyboardButton("1:1 + brainrot", callback_data='square_top_brainrot_bottom'),
        ],
        [
            InlineKeyboardButton("16:9", callback_data='full_center'),
            InlineKeyboardButton("16:9 + brainrot", callback_data='full_top_brainrot_bottom'),
        ],
        [
            InlineKeyboardButton("9:16", callback_data='face_track_9_16')
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

        if number > balance:
            topup_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(get_translation(lang, "top_up_balance_button"), callback_data='topup_start')]
            ])
            msg = await context.bot.send_message(
                chat_id=update.effective_chat.id, 
                text=get_translation(lang, "balance_is_not_enough_for_n").format(balance=balance),
                reply_markup=topup_keyboard
            )
            context.user_data['error_message_id'] = msg.message_id
            await resend_prompt(context)
            return GET_SHORTS_NUMBER

        context.user_data['config']['shorts_number'] = number
        generation_id = context.user_data.get('generation_id')
        log_event(update.effective_user.id, 'config_step_shorts_number_selected', {'choice': number, 'generation_id': generation_id})
        logger.info(f"Config for {update.effective_user.id}: shorts_number = {number}")

        keyboard = [
            [
                InlineKeyboardButton("1:1", callback_data='square_center'),
                InlineKeyboardButton("1:1 + brainrot", callback_data='square_top_brainrot_bottom'),
            ],
            [
                InlineKeyboardButton("16:9", callback_data='full_center'),
                InlineKeyboardButton("16:9 + brainrot", callback_data='full_top_brainrot_bottom'),
            ],
            [
                InlineKeyboardButton("9:16", callback_data='face_track_9_16')
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

async def get_layout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves the layout and prompts for the background video or face tracking."""
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get('lang', 'en')
    layout_choice = query.data
    context.user_data['config']['layout'] = layout_choice
    generation_id = context.user_data.get('generation_id')
    log_event(query.from_user.id, 'config_step_layout_selected', {'choice': layout_choice, 'generation_id': generation_id})
    logger.info(f"Config for {query.from_user.id}: layout = {layout_choice}")

    await query.message.delete()

    # Check if this layout is eligible for face tracking question
    if layout_choice in ['square_top_brainrot_bottom', 'face_track_9_16', 'square_center']:
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
            reply_markup=reply_markup
        )
        return GET_FACE_TRACKING

    # For other layouts, proceed as before
    if layout_choice in ['full_center']: # This was combined in the original logic
        context.user_data['config']['bottom_video'] = None
        logger.info(f"Layout for {query.from_user.id} is {layout_choice}, skipping bottom video selection.")
        
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
    else: # This covers 'full_top_brainrot_bottom'
        keyboard = [
            [
                InlineKeyboardButton(get_translation(lang, "gta_button"), callback_data='gta'),
                InlineKeyboardButton(get_translation(lang, "minecraft_button"), callback_data='minecraft'),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=open(CONFIG_EXAMPLES_DIR / 'brainrot_examples.png', 'rb'),
            caption=get_translation(lang, "choose_brainrot_video_prompt"),
            reply_markup=reply_markup
        )
        return GET_BOTTOM_VIDEO

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

    layout_choice = context.user_data['config']['layout']
    await query.message.delete()

    # Replicate the logic from the original get_layout to decide where to go next.
    if layout_choice == 'square_top_brainrot_bottom':
        # This layout needs a bottom video
        keyboard = [
            [
                InlineKeyboardButton(get_translation(lang, "gta_button"), callback_data='gta'),
                InlineKeyboardButton(get_translation(lang, "minecraft_button"), callback_data='minecraft'),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=open(CONFIG_EXAMPLES_DIR / 'brainrot_examples.png', 'rb'),
            caption=get_translation(lang, "choose_brainrot_video_prompt"),
            reply_markup=reply_markup
        )
        return GET_BOTTOM_VIDEO
    else: # face_track_9_16 or square_center
        # These layouts skip the bottom video selection
        context.user_data['config']['bottom_video'] = None
        logger.info(f"Layout for {query.from_user.id} is {layout_choice}, skipping bottom video selection.")
        
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
    lang = context.user_data.get('lang', 'en')

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
        balance = context.user_data.get('balance')
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
    lang = context.user_data.get('lang', 'en')
    choice = query.data == 'banner_yes'
    context.user_data['config']['add_banner'] = choice
    generation_id = context.user_data.get('generation_id')
    log_event(query.from_user.id, 'config_step_banner_selected', {'choice': choice, 'generation_id': generation_id})
    logger.info(f"Config for {query.from_user.id}: add_banner = {choice}")

    balance = context.user_data.get('balance')
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
    lang = context.user_data.get('lang', 'en')

    balance = context.user_data.get('balance', 0)
    shorts_number = context.user_data.get('config', {}).get('shorts_number', 'auto')

    if isinstance(shorts_number, int):
        if balance < shorts_number:
            topup_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(get_translation(lang, "top_up_balance_button"), callback_data='topup_start')]
            ])
            await query.edit_message_text(
                get_translation(lang, "insufficient_balance_for_generation").format(balance=balance, shorts_number=shorts_number),
                reply_markup=topup_keyboard
            )
            return ConversationHandler.END
    elif shorts_number == 'auto':
        pass

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
    
    queue_position = get_queue_position(task_id)

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


async def confirm_demo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the confirmation of a demo generation."""
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get('lang', 'en')

    generation_id = context.user_data.get('generation_id')
    log_event(query.from_user.id, 'demo_confirmed', {'generation_id': generation_id})

    status_message = await query.edit_message_text(text=get_translation(lang, "demo_processing_started"))
    
    asyncio.create_task(simulate_demo_processing(
        context=context,
        chat_id=query.message.chat.id,
        status_message_id=status_message.message_id
    ))

    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the current configuration and returns to the start."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    _, _, _, lang, _ = get_user(user_id)

    generation_id = context.user_data.get('generation_id')
    log_event(query.from_user.id, 'config_cancelled', {'generation_id': generation_id})

    context.user_data.clear()
    context.user_data['config'] = {}

    # Re-fetch balance
    _, balance, _, _, _ = get_user(user_id)
    context.user_data['balance'] = balance
    
    await query.edit_message_text(
        get_translation(lang, "settings_cancelled_prompt")
    )
    return GET_URL



async def broadcast_topup_package_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves the selected package from a broadcast and prompts for the payment method."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    _, _, _, lang, _ = get_user(user_id)
    context.user_data['lang'] = lang

    package_data = query.data.split('_')
    shorts = int(float(package_data[-1]))

    # Get the current prices
    discount_active = context.bot_data.get('discount_active', False)
    discount_end_time = context.bot_data.get('discount_end_time')
    is_discount_time = discount_active and discount_end_time and datetime.now(timezone.utc) < discount_end_time
    packages = get_package_prices(discount_active=is_discount_time)

    # Find the selected package
    selected_package = next((p for p in packages if p['shorts'] == shorts), None)

    if not selected_package:
        await query.message.reply_text(get_translation(lang, "something_went_wrong"))
        return ConversationHandler.END

    context.user_data['topup_package'] = selected_package
    log_event(user_id, 'topup_package_selected_from_broadcast', {'package': context.user_data['topup_package']})

    keyboard = [
        [
            InlineKeyboardButton(get_translation(lang, "telegram_stars_button"), callback_data='topup_stars'),
            InlineKeyboardButton(get_translation(lang, "cryptobot_button"), callback_data='topup_crypto'),
        ],
        [
            InlineKeyboardButton(get_translation(lang, "card_sbp_button"), callback_data='topup_yookassa'),
        ],
        [InlineKeyboardButton(get_translation(lang, "back_button"), callback_data='cancel_topup')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # We need to send a new message because we can't edit the broadcast message
    await query.message.reply_text(get_translation(lang, "topup_prompt"), reply_markup=reply_markup)
    
    return GET_TOPUP_METHOD


async def select_topup_package(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves the selected package and prompts for the payment method."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    _, _, _, lang, _ = get_user(user_id)

    package_data = query.data.split('_')
    shorts = int(package_data[2])

    # Get the current prices
    discount_active = context.bot_data.get('discount_active', False)
    discount_end_time = context.bot_data.get('discount_end_time')
    is_discount_time = discount_active and discount_end_time and datetime.now(timezone.utc) < discount_end_time
    packages = get_package_prices(discount_active=is_discount_time)

    # Find the selected package
    selected_package = next((p for p in packages if p['shorts'] == shorts), None)

    if not selected_package:
        await query.edit_message_text(get_translation(lang, "something_went_wrong"))
        return ConversationHandler.END

    context.user_data['topup_package'] = selected_package
    log_event(user_id, 'topup_package_selected', {'package': context.user_data['topup_package']})

    keyboard = [
        [
            InlineKeyboardButton(get_translation(lang, "telegram_stars_button"), callback_data='topup_stars'),
            InlineKeyboardButton(get_translation(lang, "cryptobot_button"), callback_data='topup_crypto'),
        ],
        [
            InlineKeyboardButton(get_translation(lang, "card_sbp_button"), callback_data='topup_yookassa'),
        ],
        [InlineKeyboardButton(get_translation(lang, "back_button"), callback_data='back_to_package_selection')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(get_translation(lang, "topup_prompt"), reply_markup=reply_markup)
    return GET_TOPUP_METHOD

async def topup_yookassa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Asks the user for their email for the YooKassa receipt."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    _, _, _, lang, _ = get_user(user_id)
    log_event(user_id, 'topup_method_selected', {'method': 'yookassa'})

    package = context.user_data.get('topup_package')
    if not package:
        await context.bot.send_message(chat_id=user_id, text=get_translation(lang, "something_went_wrong"))
        return ConversationHandler.END

    await query.edit_message_text(get_translation(lang, "yookassa_email_prompt"))
    return GET_YOOKASSA_EMAIL

async def get_yookassa_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the user's email and creates the YooKassa payment."""
    user_id = update.effective_user.id
    _, _, _, lang, _ = get_user(user_id)

    email = update.message.text
    # Basic email validation (can be expanded)
    if "@" not in email or "." not in email:
        await update.message.reply_text(get_translation(lang, "invalid_email_format"))
        return GET_YOOKASSA_EMAIL

    context.user_data['yookassa_email'] = email
    log_event(user_id, 'yookassa_email_provided', {'email': email})

    package = context.user_data.get('topup_package')
    if not package:
        await update.message.reply_text(get_translation(lang, "something_went_wrong"))
        return ConversationHandler.END

    amount = package['shorts']
    total_price = package['rub']

    Configuration.configure(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)

    idempotence_key = str(uuid.uuid4())
    payment = await asyncio.to_thread(
        yookassa.Payment.create,
        {
            "amount": {
                "value": str(total_price),
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": f"https://t.me/{context.bot.username}"
            },
            "capture": True,
            "description": f"Top-up for {amount} shorts",
            "metadata": {
                "user_id": user_id,
                "shorts_amount": amount
            },
            "receipt": {
                "customer": {
                    "email": email
                },
                "items": [
                    {
                        "description": f"Top-up for {amount} shorts",
                        "quantity": "1.00",
                        "amount": {
                            "value": str(total_price),
                            "currency": "RUB"
                        },
                        "vat_code": 1,
                        "payment_mode": "full_prepayment",
                        "payment_subject": "service"
                    }
                ]
            }
        },
        idempotence_key
    )

    payment_url = payment.confirmation.confirmation_url
    payment_id = payment.id
    context.user_data['yookassa_payment_id'] = payment_id

    payload = f"check_yookassa:{user_id}:{amount}"

    keyboard = [
        [InlineKeyboardButton(get_translation(lang, "pay_button"), url=payment_url)],
        [InlineKeyboardButton(get_translation(lang, "check_payment_button"), callback_data=payload)],
        [InlineKeyboardButton(get_translation(lang, "back_button"), callback_data='back_to_package_selection')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        get_translation(lang, "yookassa_payment_details").format(
            payment_id=payment_id,
            total_price=total_price
        ),
        reply_markup=reply_markup,
        parse_mode="HTML",
        disable_web_page_preview=True
    )

    return YOOKASSA_PAYMENT


async def check_yookassa_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Checks the YooKassa payment and updates the balance."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    _, _, _, lang, _ = get_user(user_id)

    try:
        identifier, user_id_str, amount_str = query.data.split(':')
        user_id_from_payload = int(user_id_str)
        amount = int(amount_str)
        payment_id = context.user_data.get('yookassa_payment_id')

        if not payment_id:
            await query.edit_message_text(get_translation(lang, "payment_check_error"))
            return YOOKASSA_PAYMENT

        Configuration.configure(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)
        payment = await asyncio.to_thread(yookassa.Payment.find_one, payment_id)

        if payment.status == 'succeeded':
            if 'payment_not_found_messages' in context.user_data:
                for message_id in context.user_data['payment_not_found_messages']:
                    try:
                        await context.bot.delete_message(chat_id=user_id_from_payload, message_id=message_id)
                    except Exception as e:
                        logger.warning(f"Could not delete message {message_id}: {e}")
                del context.user_data['payment_not_found_messages']

            add_to_user_balance(user_id_from_payload, amount)
            _, new_balance, _, _, _ = get_user(user_id_from_payload)
            log_event(user_id_from_payload, 'payment_success', {'provider': 'yookassa', 'shorts_amount': amount, 'total_amount': float(payment.amount.value), 'currency': payment.amount.currency})

            await query.edit_message_text(
                get_translation(lang, "payment_successful").format(amount=amount, new_balance=new_balance),
                parse_mode="HTML"
            )
            return ConversationHandler.END
        elif payment.status == 'pending' or payment.status == 'waiting_for_capture':
            msg = await context.bot.send_message(chat_id=user_id_from_payload, text=get_translation(lang, "payment_not_found_try_again"))
            if 'payment_not_found_messages' not in context.user_data:
                context.user_data['payment_not_found_messages'] = []
            context.user_data['payment_not_found_messages'].append(msg.message_id)
            return YOOKASSA_PAYMENT
        else: # canceled, etc.
            await query.edit_message_text(get_translation(lang, "payment_failed").format(status=payment.status))
            return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error checking YooKassa payment: {e}", exc_info=True)
        await query.edit_message_text(get_translation(lang, "payment_check_error"))
        return YOOKASSA_PAYMENT


async def back_to_package_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Goes back to the package selection screen."""
    from commands import topup_start
    return await topup_start(update, context)

async def topup_stars(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Sends an invoice for the selected package using Telegram Stars."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    _, _, _, lang, _ = get_user(user_id)
    log_event(user_id, 'topup_method_selected', {'method': 'telegram_stars'})

    await query.delete_message()
    
    package = context.user_data.get('topup_package')
    if not package:
        await context.bot.send_message(chat_id=user_id, text=get_translation(lang, "something_went_wrong"))
        return ConversationHandler.END

    shorts_amount = package['shorts']
    stars_amount = package['stars']
    
    chat_id = update.effective_chat.id
    title = get_translation(lang, "topup_invoice_title").format(shorts_amount=shorts_amount)
    description = get_translation(lang, "topup_invoice_description").format(shorts_amount=shorts_amount)
    payload = f"topup-{chat_id}-{shorts_amount}-{stars_amount}"
    currency = "XTR"
    prices = [LabeledPrice(get_translation(lang, "n_shorts").format(shorts_amount=shorts_amount), stars_amount)]

    await context.bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description=description,
        payload=payload,
        provider_token=None,
        currency=currency,
        prices=prices
    )
    return ConversationHandler.END

async def topup_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the crypto payment for the selected package."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    _, _, _, lang, _ = get_user(user_id)
    log_event(user_id, 'topup_method_selected', {'method': 'cryptobot'})

    package = context.user_data.get('topup_package')
    if not package:
        await context.bot.send_message(chat_id=user_id, text=get_translation(lang, "something_went_wrong"))
        return ConversationHandler.END

    amount = package['shorts']
    total_price = package['usdt']

    crypto = AioCryptoPay(token=CRYPTO_BOT_TOKEN, network=Networks.MAIN_NET)
    invoice = await crypto.create_invoice(asset='USDT', amount=total_price)
    await crypto.close()

    payment_url = invoice.bot_invoice_url
    invoice_id = invoice.invoice_id

    payload = f"check_crypto:{user_id}:{amount}:{invoice_id}"

    keyboard = [
        [InlineKeyboardButton(get_translation(lang, "pay_button"), url=payment_url)],
        [InlineKeyboardButton(get_translation(lang, "check_payment_button"), callback_data=payload)],
        [InlineKeyboardButton(get_translation(lang, "back_button"), callback_data='back_to_package_selection')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        get_translation(lang, "you_are_buying_n_shorts_for_m_usdt").format(amount=amount, total_price=total_price),
        reply_markup=reply_markup,
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    
    return CRYPTO_PAYMENT

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: 
    """Answers the PreCheckoutQuery."""
    query = update.pre_checkout_query
    user_id = query.from_user.id
    _, _, _, lang, _ = get_user(user_id)
    if query.invoice_payload.startswith('topup-'):
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message=get_translation(lang, "something_went_wrong"))

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: 
    """Confirms the successful payment."""
    payment_info = update.message.successful_payment
    payload_parts = payment_info.invoice_payload.split('-')
    user_id = int(payload_parts[1])
    shorts_amount = int(payload_parts[2])

    add_to_user_balance(user_id, shorts_amount)
    _, new_balance, _, lang, _ = get_user(user_id)

    log_event(user_id, 'payment_success', {'provider': 'telegram_stars', 'shorts_amount': shorts_amount, 'total_amount': payment_info.total_amount, 'currency': payment_info.currency})

    await context.bot.send_message(
        chat_id=user_id,
        text=get_translation(lang, "payment_successful").format(amount=shorts_amount, new_balance=new_balance),
        parse_mode="HTML"
    )

from aiocryptopay import AioCryptoPay, Networks

async def check_crypto_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Checks the crypto payment and updates the balance."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    _, _, _, lang, _ = get_user(user_id)

    try:
        identifier, user_id_str, amount_str, invoice_id = query.data.split(':')
        user_id_from_payload = int(user_id_str)
        amount = int(amount_str)

        try:
            crypto = AioCryptoPay(token=CRYPTO_BOT_TOKEN, network=Networks.MAIN_NET)
            invoices = await crypto.get_invoices(invoice_ids=invoice_id)
            await crypto.close()

            if invoices and invoices[0].status == 'paid':
                if 'payment_not_found_messages' in context.user_data:
                    for message_id in context.user_data['payment_not_found_messages']:
                        try:
                            await context.bot.delete_message(chat_id=user_id_from_payload, message_id=message_id)
                        except Exception as e:
                            logger.warning(f"Could not delete message {message_id}: {e}")
                    del context.user_data['payment_not_found_messages']

                add_to_user_balance(user_id_from_payload, amount)
                _, new_balance, _, _, _ = get_user(user_id_from_payload)
                log_event(user_id_from_payload, 'payment_success', {'provider': 'cryptobot', 'shorts_amount': amount, 'total_amount': invoices[0].amount, 'currency': invoices[0].asset})

                await query.edit_message_text(
                    get_translation(lang, "payment_successful").format(amount=amount, new_balance=new_balance),
                    parse_mode="HTML"
                )
                return ConversationHandler.END
            else:
                msg = await context.bot.send_message(chat_id=user_id_from_payload, text=get_translation(lang, "payment_not_found_try_again"))
                if 'payment_not_found_messages' not in context.user_data:
                    context.user_data['payment_not_found_messages'] = []
                context.user_data['payment_not_found_messages'].append(msg.message_id)
                return CRYPTO_PAYMENT

        except Exception as e:
            logger.error(f"Error checking crypto payment with aiocryptopay: {e}", exc_info=True)
            await query.edit_message_text(get_translation(lang, "payment_system_error"))
            return CRYPTO_PAYMENT

    except (ValueError, IndexError) as e:
        logger.error(f"Error checking crypto payment: {e}", exc_info=True)
        await query.edit_message_text(get_translation(lang, "payment_check_error"))
        return CRYPTO_PAYMENT

async def cancel_topup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the top-up process."""
    query = update.callback_query
    await query.answer()
    await query.delete_message()
    return ConversationHandler.END

async def handle_rating(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the user's rating and asks for text feedback."""
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get('lang', 'en')
    rating = query.data.split('_')[1]
    
    rating_id = str(uuid.uuid4())
    generation_id = context.user_data.get('generation_id')
    log_event(query.from_user.id, 'rating', {'rating_id': rating_id, 'rating': rating, 'generation_id': generation_id})
    
    context.user_data['rating_id'] = rating_id
    context.user_data['rating'] = rating

    await query.edit_message_text(
        text=get_translation(lang, "thank_you_for_rating_leave_feedback")
    )
    return FEEDBACK

async def handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the user's text feedback and forwards it."""
    user_id = update.message.from_user.id
    lang = context.user_data.get('lang', 'en')
    rating_id = context.user_data.get('rating_id')
    rating = context.user_data.get('rating')

    log_event(user_id, 'feedback', {'rating_id': rating_id})

    if FEEDBACK_GROUP_ID:
        try:
            await context.bot.forward_message(
                chat_id=FEEDBACK_GROUP_ID,
                from_chat_id=update.message.chat_id,
                message_id=update.message.message_id
            )
            # Optionally, send the rating as well
            if rating:
                await context.bot.send_message(
                    chat_id=FEEDBACK_GROUP_ID,
                    text=get_translation(lang, "user_rated").format(user_id=user_id, rating=rating)
                )
        except Exception as e:
            logger.error(f"Failed to forward feedback to group {FEEDBACK_GROUP_ID}: {e}")

    await update.message.reply_text(get_translation(lang, "thank_you_for_feedback"))
    context.user_data.clear()
    return ConversationHandler.END

async def handle_user_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the user's text feedback and forwards it with approval buttons."""
    user_id = update.message.from_user.id
    _, _, _, lang, _ = get_user(user_id)
    feedback_text = update.message.text

    if FEEDBACK_GROUP_ID:
        try:
            keyboard = [
                [
                    InlineKeyboardButton(get_translation(lang, "approve"), callback_data=f'approve_feedback:{user_id}'),
                    InlineKeyboardButton(get_translation(lang, "decline"), callback_data=f'decline_feedback:{user_id}')
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_message(
                chat_id=FEEDBACK_GROUP_ID,
                text=get_translation(lang, "feedback_from_user_with_text").format(user_id=user_id, feedback_text=feedback_text),
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Failed to forward feedback to group {FEEDBACK_GROUP_ID}: {e}")

    await update.message.reply_text(get_translation(lang, "thank_you_for_feedback"))
    return ConversationHandler.END

async def handle_feedback_approval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the approval or decline of feedback."""
    query = update.callback_query
    await query.answer()

    admin_user = query.from_user
    data = query.data.split(':')
    action = data[0]
    user_id = int(data[1])

    original_message = query.message.text
    
    _, _, _, lang, _ = get_user(user_id)

    if action == 'approve_feedback':
        add_to_user_balance(user_id, REWARD_FOR_FEEDBACK)
        await query.edit_message_text(
            f"{original_message}\n\n---\nApproved by {admin_user.full_name} (@{admin_user.username})\n+ {REWARD_FOR_FEEDBACK} shorts for user {user_id}"
        )
        await context.bot.send_message(
            chat_id=user_id,
            text=get_translation(lang, "feedback_approved").format(reward=REWARD_FOR_FEEDBACK)
        )
    elif action == 'decline_feedback':
        await query.edit_message_text(
            f"{original_message}\n\n---\nDeclined by {admin_user.full_name} (@{admin_user.username})"
        )

async def skip_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Skips the text feedback step."""
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get('lang', 'en')
    await query.edit_message_text(get_translation(lang, "thank_you_for_rating"))
    context.user_data.clear()
    return ConversationHandler.END

async def back_to_package_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Goes back to the package selection screen."""
    from commands import topup_start
    return await topup_start(update, context)

async def topup_stars(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Sends an invoice for the selected package using Telegram Stars."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    _, _, _, lang, _ = get_user(user_id)
    log_event(user_id, 'topup_method_selected', {'method': 'telegram_stars'})

    await query.delete_message()
    
    package = context.user_data.get('topup_package')
    if not package:
        await context.bot.send_message(chat_id=user_id, text=get_translation(lang, "something_went_wrong"))
        return ConversationHandler.END

    shorts_amount = package['shorts']
    stars_amount = package['stars']
    
    chat_id = update.effective_chat.id
    title = get_translation(lang, "topup_invoice_title").format(shorts_amount=shorts_amount)
    description = get_translation(lang, "topup_invoice_description").format(shorts_amount=shorts_amount)
    payload = f"topup-{chat_id}-{shorts_amount}-{stars_amount}"
    currency = "XTR"
    prices = [LabeledPrice(get_translation(lang, "n_shorts").format(shorts_amount=shorts_amount), stars_amount)]

    await context.bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description=description,
        payload=payload,
        provider_token=None,
        currency=currency,
        prices=prices
    )
    return ConversationHandler.END

async def topup_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the crypto payment for the selected package."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    _, _, _, lang, _ = get_user(user_id)
    log_event(user_id, 'topup_method_selected', {'method': 'cryptobot'})

    package = context.user_data.get('topup_package')
    if not package:
        await context.bot.send_message(chat_id=user_id, text=get_translation(lang, "something_went_wrong"))
        return ConversationHandler.END

    amount = package['shorts']
    total_price = package['usdt']

    crypto = AioCryptoPay(token=CRYPTO_BOT_TOKEN, network=Networks.MAIN_NET)
    invoice = await crypto.create_invoice(asset='USDT', amount=total_price)
    await crypto.close()

    payment_url = invoice.bot_invoice_url
    invoice_id = invoice.invoice_id

    payload = f"check_crypto:{user_id}:{amount}:{invoice_id}"

    keyboard = [
        [InlineKeyboardButton(get_translation(lang, "pay_button"), url=payment_url)],
        [InlineKeyboardButton(get_translation(lang, "check_payment_button"), callback_data=payload)],
        [InlineKeyboardButton(get_translation(lang, "back_button"), callback_data='back_to_package_selection')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        get_translation(lang, "you_are_buying_n_shorts_for_m_usdt").format(amount=amount, total_price=total_price),
        reply_markup=reply_markup,
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    
    return CRYPTO_PAYMENT

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: 
    """Answers the PreCheckoutQuery."""
    query = update.pre_checkout_query
    user_id = query.from_user.id
    _, _, _, lang, _ = get_user(user_id)
    if query.invoice_payload.startswith('topup-'):
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message=get_translation(lang, "something_went_wrong"))

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: 
    """Confirms the successful payment."""
    payment_info = update.message.successful_payment
    payload_parts = payment_info.invoice_payload.split('-')
    user_id = int(payload_parts[1])
    shorts_amount = int(payload_parts[2])

    add_to_user_balance(user_id, shorts_amount)
    _, new_balance, _, lang, _ = get_user(user_id)

    log_event(user_id, 'payment_success', {'provider': 'telegram_stars', 'shorts_amount': shorts_amount, 'total_amount': payment_info.total_amount, 'currency': payment_info.currency})

    await context.bot.send_message(
        chat_id=user_id,
        text=get_translation(lang, "payment_successful").format(amount=shorts_amount, new_balance=new_balance),
        parse_mode="HTML"
    )

from aiocryptopay import AioCryptoPay, Networks

async def check_crypto_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Checks the crypto payment and updates the balance."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    _, _, _, lang, _ = get_user(user_id)

    try:
        identifier, user_id_str, amount_str, invoice_id = query.data.split(':')
        user_id_from_payload = int(user_id_str)
        amount = int(amount_str)

        try:
            crypto = AioCryptoPay(token=CRYPTO_BOT_TOKEN, network=Networks.MAIN_NET)
            invoices = await crypto.get_invoices(invoice_ids=invoice_id)
            await crypto.close()

            if invoices and invoices[0].status == 'paid':
                if 'payment_not_found_messages' in context.user_data:
                    for message_id in context.user_data['payment_not_found_messages']:
                        try:
                            await context.bot.delete_message(chat_id=user_id_from_payload, message_id=message_id)
                        except Exception as e:
                            logger.warning(f"Could not delete message {message_id}: {e}")
                    del context.user_data['payment_not_found_messages']

                add_to_user_balance(user_id_from_payload, amount)
                _, new_balance, _, _, _ = get_user(user_id_from_payload)
                log_event(user_id_from_payload, 'payment_success', {'provider': 'cryptobot', 'shorts_amount': amount, 'total_amount': invoices[0].amount, 'currency': invoices[0].asset})

                await query.edit_message_text(
                    get_translation(lang, "payment_successful").format(amount=amount, new_balance=new_balance),
                    parse_mode="HTML"
                )
                return ConversationHandler.END
            else:
                msg = await context.bot.send_message(chat_id=user_id_from_payload, text=get_translation(lang, "payment_not_found_try_again"))
                if 'payment_not_found_messages' not in context.user_data:
                    context.user_data['payment_not_found_messages'] = []
                context.user_data['payment_not_found_messages'].append(msg.message_id)
                return CRYPTO_PAYMENT

        except Exception as e:
            logger.error(f"Error checking crypto payment with aiocryptopay: {e}", exc_info=True)
            await query.edit_message_text(get_translation(lang, "payment_system_error"))
            return CRYPTO_PAYMENT

    except (ValueError, IndexError) as e:
        logger.error(f"Error checking crypto payment: {e}", exc_info=True)
        await query.edit_message_text(get_translation(lang, "payment_check_error"))
        return CRYPTO_PAYMENT



async def handle_rating(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the user's rating and asks for text feedback."""
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get('lang', 'en')
    rating = query.data.split('_')[1]
    
    rating_id = str(uuid.uuid4())
    generation_id = context.user_data.get('generation_id')
    log_event(query.from_user.id, 'rating', {'rating_id': rating_id, 'rating': rating, 'generation_id': generation_id})
    
    context.user_data['rating_id'] = rating_id
    context.user_data['rating'] = rating

    await query.edit_message_text(
        text=get_translation(lang, "thank_you_for_rating_leave_feedback")
    )
    return FEEDBACK

async def handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the user's text feedback and forwards it."""
    user_id = update.message.from_user.id
    lang = context.user_data.get('lang', 'en')
    rating_id = context.user_data.get('rating_id')
    rating = context.user_data.get('rating')

    log_event(user_id, 'feedback', {'rating_id': rating_id})

    if FEEDBACK_GROUP_ID:
        try:
            await context.bot.forward_message(
                chat_id=FEEDBACK_GROUP_ID,
                from_chat_id=update.message.chat_id,
                message_id=update.message.message_id
            )
            # Optionally, send the rating as well
            if rating:
                await context.bot.send_message(
                    chat_id=FEEDBACK_GROUP_ID,
                    text=get_translation(lang, "user_rated").format(user_id=user_id, rating=rating)
                )
        except Exception as e:
            logger.error(f"Failed to forward feedback to group {FEEDBACK_GROUP_ID}: {e}")

    await update.message.reply_text(get_translation(lang, "thank_you_for_feedback"))
    context.user_data.clear()
    return ConversationHandler.END

async def handle_user_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the user's text feedback and forwards it with approval buttons."""
    user_id = update.message.from_user.id
    _, _, _, lang, _ = get_user(user_id)
    feedback_text = update.message.text

    if FEEDBACK_GROUP_ID:
        try:
            keyboard = [
                [
                    InlineKeyboardButton(get_translation(lang, "approve"), callback_data=f'approve_feedback:{user_id}'),
                    InlineKeyboardButton(get_translation(lang, "decline"), callback_data=f'decline_feedback:{user_id}')
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_message(
                chat_id=FEEDBACK_GROUP_ID,
                text=get_translation(lang, "feedback_from_user_with_text").format(user_id=user_id, feedback_text=feedback_text),
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Failed to forward feedback to group {FEEDBACK_GROUP_ID}: {e}")

    await update.message.reply_text(get_translation(lang, "thank_you_for_feedback"))
    return ConversationHandler.END

async def handle_feedback_approval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the approval or decline of feedback."""
    query = update.callback_query
    await query.answer()

    admin_user = query.from_user
    data = query.data.split(':')
    action = data[0]
    user_id = int(data[1])

    original_message = query.message.text
    
    _, _, _, lang, _ = get_user(user_id)

    if action == 'approve_feedback':
        add_to_user_balance(user_id, REWARD_FOR_FEEDBACK)
        await query.edit_message_text(
            f"{original_message}\n\n---\nApproved by {admin_user.full_name} (@{admin_user.username})\n+ {REWARD_FOR_FEEDBACK} shorts for user {user_id}"
        )
        await context.bot.send_message(
            chat_id=user_id,
            text=get_translation(lang, "feedback_approved").format(reward=REWARD_FOR_FEEDBACK)
        )
    elif action == 'decline_feedback':
        await query.edit_message_text(
            f"{original_message}\n\n---\nDeclined by {admin_user.full_name} (@{admin_user.username})"
        )

async def skip_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Skips the text feedback step."""
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get('lang', 'en')
    await query.edit_message_text(get_translation(lang, "thank_you_for_rating"))
    context.user_data.clear()
    return ConversationHandler.END

