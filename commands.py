import logging
import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeChat
from telegram.ext import ContextTypes, ConversationHandler
from telegram.error import TelegramError
from database import get_user, add_to_user_balance, set_user_balance, get_all_user_ids
from analytics import log_event
from states import GET_URL, GET_TOPUP_METHOD, GET_BROADCAST_MESSAGE
from config import TUTORIAL_LINK
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало диалога, запрашивает URL."""
    user_id = update.effective_user.id
    _, balance, _, is_new = get_user(user_id)

    if is_new:
        log_event(user_id, 'new_user', {'username': update.effective_user.username})

    # Set commands for the user
    admin_ids_str = os.environ.get("ADMIN_USER_IDS", "")
    admin_ids = [id.strip() for id in admin_ids_str.split(',')]
    logger.info(f"Admin IDs: {admin_ids}")
    logger.info(f"User ID: {user_id}")

    base_commands = [
        BotCommand(command="start", description="Начать работу"),
        BotCommand(command="help", description="Помощь и описание"),
        BotCommand(command="balance", description="Показать баланс"),
        BotCommand(command="topup", description="Пополнить баланс"),
    ]
    if str(user_id) in admin_ids:
        logger.info("User is an admin, adding admin commands.")
        base_commands.append(BotCommand(command="addshorts", description="Добавить шортсы пользователю"))
        base_commands.append(BotCommand(command="setbalance", description="Установить баланс пользователю"))
        base_commands.append(BotCommand(command="broadcast", description="Сделать рассылку"))
        base_commands.append(BotCommand(command="start_discount", description="Начать скидку"))
        base_commands.append(BotCommand(command="end_discount", description="Завершить скидку"))
    
    await context.bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=user_id))
    await context.bot.set_my_commands(base_commands, scope=BotCommandScopeChat(chat_id=user_id))



    context.user_data.clear()
    context.user_data['config'] = {}
    context.user_data['balance'] = balance
    
    await update.message.reply_text(
        f"Привет!\nПришлите мне ссылку на YouTube видео, и я сделаю из него короткие виральные ролики для YT Shorts/Reels/Tiktok.\nВаш баланс: {balance} шортсов.\n\n👉 <a href='{TUTORIAL_LINK}'>Инструкция (1 мин. чтения)</a>",
        parse_mode="HTML"
    )
    return GET_URL

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет сообщение с помощью и списком команд."""
    help_text = (
        "Этот бот создает короткие вирусные видео из YouTube роликов.\n\n"
        "Доступные команды:\n"
        "/start - Начать работу с ботом\n"
        "/help - Показать это сообщение\n"
        "/balance - Показать текущий баланс\n"
        "/topup - Пополнить баланс\n"
        "@sf_tsupport_bot - по вопросам и поддержке\n\n"
        f"👉 <a href='{TUTORIAL_LINK}'>Инструкция (1 мин. чтения)</a>"
    )
    await update.message.reply_text(help_text)

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет текущий баланс пользователя."""
    user_id = update.effective_user.id
    _, balance, _, _ = get_user(user_id)
    keyboard = [
        [InlineKeyboardButton("Пополнить баланс", callback_data='topup_start')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Ваш текущий баланс: {balance} шортсов.", reply_markup=reply_markup)

async def topup_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the top-up process."""
    keyboard = [
        [
            InlineKeyboardButton("⭐️ Telegram Stars", callback_data='topup_stars'),
            InlineKeyboardButton("💎 CryptoBot", callback_data='topup_crypto'),
        ],
        [InlineKeyboardButton("❌ Отмена", callback_data='cancel_topup')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Выберите способ пополнения:", reply_markup=reply_markup)
    else:
        await update.message.reply_text("Выберите способ пополнения:", reply_markup=reply_markup)
    return GET_TOPUP_METHOD


async def add_shorts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Adds a specified amount of shorts to a user's balance."""
    admin_ids_str = os.environ.get("ADMIN_USER_IDS", "")
    admin_ids = [id.strip() for id in admin_ids_str.split(',')]
    if str(update.effective_user.id) not in admin_ids:
        return

    try:
        user_id_str, amount_str = context.args
        user_id = int(user_id_str)
        amount = int(amount_str)

        if amount <= 0:
            await update.message.reply_text("Количество шортсов должно быть положительным числом.")
            return

        add_to_user_balance(user_id, amount)
        _, new_balance, _, _ = get_user(user_id)

        await update.message.reply_text(f"Баланс пользователя {user_id} успешно пополнен на {amount} шортсов. Новый баланс: {new_balance}.")

    except (ValueError, IndexError):
        await update.message.reply_text("Неверный формат команды. Используйте: /addshorts <user_id> <amount>")

async def set_user_balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sets a user's balance to a specified amount."""
    admin_ids_str = os.environ.get("ADMIN_USER_IDS", "")
    admin_ids = [id.strip() for id in admin_ids_str.split(',')]
    if str(update.effective_user.id) not in admin_ids:
        return

    try:
        user_id_str, amount_str = context.args
        user_id = int(user_id_str)
        amount = int(amount_str)

        if amount < 0:
            await update.message.reply_text("Баланс не может быть отрицательным.")
            return

        set_user_balance(user_id, amount)
        _, new_balance, _, _ = get_user(user_id)

        await update.message.reply_text(f"Баланс пользователя {user_id} установлен в {new_balance}.")

    except (ValueError, IndexError):
        await update.message.reply_text("Неверный формат команды. Используйте: /setbalance <user_id> <amount>")


async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the broadcast conversation."""
    admin_ids_str = os.environ.get("ADMIN_USER_IDS", "")
    admin_ids = [id.strip() for id in admin_ids_str.split(',')]
    if str(update.effective_user.id) not in admin_ids:
        return ConversationHandler.END

    await update.message.reply_text("Отправьте пост, который нужно разослать юзерам. Вы можете отменить рассылку командой /cancel.")
    return GET_BROADCAST_MESSAGE


async def broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Sends a broadcast message to all users, respecting rate limits."""
    text = update.message.text
    entities = update.message.entities
    caption = update.message.caption
    caption_entities = update.message.caption_entities
    photo = update.message.photo[-1].file_id if update.message.photo else None
    animation = update.message.animation.file_id if update.message.animation else None

    user_ids = get_all_user_ids()
    sent_count = 0
    failed_count = 0

    await update.message.reply_text(f"Начинаю рассылку для {len(user_ids)} пользователей...")

    for user_id in user_ids:
        try:
            if photo:
                await context.bot.send_photo(chat_id=user_id, photo=photo, caption=caption, caption_entities=caption_entities)
            elif animation:
                await context.bot.send_animation(chat_id=user_id, animation=animation, caption=caption, caption_entities=caption_entities)
            elif text:
                await context.bot.send_message(chat_id=user_id, text=text, entities=entities)
            sent_count += 1
        except TelegramError as e:
            logger.error(f"Failed to send message to {user_id}: {e}")
            failed_count += 1
        
        await asyncio.sleep(0.04) # ~25 messages per second

    await update.message.reply_text(f"Рассылка завершена. Отправлено: {sent_count}. Ошибок: {failed_count}.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the current conversation."""
    context.user_data.clear()
    context.user_data['config'] = {}
    await update.message.reply_text(
        "Действие отменено. Пришлите мне ссылку на YouTube видео, чтобы начать заново."
    )
    return ConversationHandler.END

async def start_discount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Starts a discount period."""
    admin_ids_str = os.environ.get("ADMIN_USER_IDS", "")
    admin_ids = [id.strip() for id in admin_ids_str.split(',')]
    if str(update.effective_user.id) not in admin_ids:
        return

    if not context.args:
        await update.message.reply_text("Пожалуйста, укажите время окончания скидки. Формат: YYYY-MM-DD HH:MM")
        return

    try:
        end_time_str = " ".join(context.args)
        end_time_naive = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M")
        end_time_utc = end_time_naive.replace(tzinfo=timezone.utc)
        
        context.bot_data['discount_active'] = True
        context.bot_data['discount_end_time'] = end_time_utc
        
        await update.message.reply_text(f"✅ Скидка началась и продлится до {end_time_str} UTC.")
        
    except (ValueError, IndexError):
        await update.message.reply_text("Неверный формат даты. Используйте: YYYY-MM-DD HH:MM")

async def end_discount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ends a discount period immediately."""
    admin_ids_str = os.environ.get("ADMIN_USER_IDS", "")
    admin_ids = [id.strip() for id in admin_ids_str.split(',')]
    if str(update.effective_user.id) not in admin_ids:
        return

    context.bot_data['discount_active'] = False
    context.bot_data.pop('discount_end_time', None)
    
    await update.message.reply_text("✅ Скидка завершена.")
