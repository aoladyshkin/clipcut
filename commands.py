import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeChat
from telegram.ext import ContextTypes, ConversationHandler
from database import get_user, add_to_user_balance, set_user_balance, get_all_user_ids
from states import GET_URL, GET_TOPUP_METHOD

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало диалога, запрашивает URL."""
    user_id = update.effective_user.id
    user = get_user(user_id)
    _, balance, _ = user

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
    
    await context.bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=user_id))
    await context.bot.set_my_commands(base_commands, scope=BotCommandScopeChat(chat_id=user_id))

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

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет сообщение с помощью и списком команд."""
    help_text = (
        "Этот бот создает короткие вирусные видео из YouTube роликов.\n\n"
        "Доступные команды:\n"
        "/start - Начать работу с ботом\n"
        "/help - Показать это сообщение\n"
        "/balance - Показать текущий баланс\n"
        "/topup - Пополнить баланс"
    )
    await update.message.reply_text(help_text)

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет текущий баланс пользователя."""
    user_id = update.effective_user.id
    _, balance, _ = get_user(user_id)
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
        _, new_balance, _ = get_user(user_id)

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
        _, new_balance, _ = get_user(user_id)

        await update.message.reply_text(f"Баланс пользователя {user_id} установлен в {new_balance}.")

    except (ValueError, IndexError):
        await update.message.reply_text("Неверный формат команды. Используйте: /setbalance <user_id> <amount>")

from states import GET_URL, GET_TOPUP_METHOD, GET_BROADCAST_MESSAGE

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the broadcast conversation."""
    admin_ids_str = os.environ.get("ADMIN_USER_IDS", "")
    admin_ids = [id.strip() for id in admin_ids_str.split(',')]
    if str(update.effective_user.id) not in admin_ids:
        return ConversationHandler.END

    await update.message.reply_text("Отправьте пост, который нужно разослать юзерам. Вы можете отменить рассылку командой /cancel.")
    return GET_BROADCAST_MESSAGE

async def broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Sends a broadcast message to all users."""
    text = update.message.text or update.message.caption
    photo = update.message.photo[-1].file_id if update.message.photo else None
    animation = update.message.animation.file_id if update.message.animation else None

    user_ids = get_all_user_ids()
    sent_count = 0
    failed_count = 0

    await update.message.reply_text(f"Начинаю рассылку для {len(user_ids)} пользователей...")

    for user_id in user_ids:
        try:
            if photo:
                await context.bot.send_photo(chat_id=user_id, photo=photo, caption=text)
            elif animation:
                await context.bot.send_animation(chat_id=user_id, animation=animation, caption=text)
            elif text:
                await context.bot.send_message(chat_id=user_id, text=text)
            sent_count += 1
        except Exception as e:
            logger.error(f"Failed to send message to {user_id}: {e}")
            failed_count += 1

    await update.message.reply_text(f"Рассылка завершена. Отправлено: {sent_count}. Ошибок: {failed_count}.")
    return ConversationHandler.END

async def cancel_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the broadcast."""
    await update.message.reply_text("Рассылка отменена.")
    return ConversationHandler.END