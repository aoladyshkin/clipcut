import logging
import asyncio
import uuid
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.error import BadRequest
from database import get_user
from pricing import DEMO_CONFIG
from analytics import log_event
from states import CONFIRM_CONFIG
from localization import get_translation
from utils import format_config
from processing.demo import simulate_demo_processing

logger = logging.getLogger(__name__)

async def start_demo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the demo process by loading the demo config."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    _, _, _, lang, _ = get_user(user_id)
    context.user_data['lang'] = lang

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
