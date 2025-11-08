import logging
import uuid
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database import get_user, add_to_user_balance
from analytics import log_event
from localization import get_translation
from config import MODERATORS_GROUP_ID, MODERATORS_USER_TAGS, FEEDBACK_GROUP_ID, REWARD_FOR_FEEDBACK
from states import FEEDBACK

logger = logging.getLogger(__name__)

async def handle_dislike_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the dislike button click."""
    query = update.callback_query

    user_id = query.from_user.id
    message_id = query.message.message_id
    chat_id = query.message.chat.id

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
                from_chat_id=update.message.chat.id,
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
            f"{original_message}\n\n---\nApproved by {admin_user.full_name} (@{admin_user.username})\n+ {REWARD_FOR_FEEDBACK} generations for user {user_id}"
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
