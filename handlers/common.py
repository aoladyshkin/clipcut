import logging
from telegram import Update
from telegram.ext import ContextTypes
from database import get_user
from analytics import log_event
from localization import get_translation
from states import GET_URL

logger = logging.getLogger(__name__)

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

    await query.edit_message_text(
        get_translation(lang, "settings_cancelled_prompt")
    )
    return GET_URL
