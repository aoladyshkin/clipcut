import logging
import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeChat
from telegram.ext import ContextTypes, ConversationHandler
from telegram.error import TelegramError
from database import get_user, add_to_user_balance, set_user_balance, get_all_user_ids, delete_user
from analytics import log_event
from states import GET_URL, GET_TOPUP_METHOD, GET_BROADCAST_MESSAGE, GET_FEEDBACK_TEXT, GET_TARGETED_BROADCAST_MESSAGE
from config import TUTORIAL_LINK
from datetime import datetime, timezone

import csv
import io
from database import get_all_users_data

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало диалога, запрашивает URL."""
    user_id = update.effective_user.id
    log_event(user_id, 'start_command', {'username': update.effective_user.username})
    
    referrer_id = None
    source = None
    if context.args:
        payload = context.args[0]
        if payload.startswith('ref_'):
            try:
                referrer_id = int(payload.split('_')[1])
                source = 'referral'
            except (IndexError, ValueError):
                referrer_id = None
        elif payload.startswith('source_'):
            try:
                source = payload.split('_')[1]
            except IndexError:
                source = None

    _, balance, _, is_new = get_user(user_id, referrer_id=referrer_id, source=source)

    if is_new:
        log_event(user_id, 'new_user', {'username': update.effective_user.username, 'referrer_id': referrer_id, 'source': source})
        if referrer_id and referrer_id != user_id:
            # Award bonuses
            add_to_user_balance(user_id, 10)
            add_to_user_balance(referrer_id, 10)
            
            # Update local balance for the new user
            balance += 10
            
            await update.message.reply_text("🎉 Добро пожаловать! Вы получили 10 бонусных шортсов за использование реферальной ссылки.")
            
            try:
                # Try to get the new user's username to mention them
                new_user_mention = f"@{update.effective_user.username}" if update.effective_user.username else f"пользователь {user_id}"
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=f"🎉 Ваш друг {new_user_mention} присоединился по вашей ссылке! Вы получили 10 бонусных шортсов."
                )
            except Exception as e:
                logger.error(f"Failed to send referral notification to {referrer_id}: {e}")

    # Set commands for the user
    admin_ids_str = os.environ.get("ADMIN_USER_IDS", "")
    admin_ids = [id.strip() for id in admin_ids_str.split(',')]
    

    base_commands = [
        BotCommand(command="start", description="Начать работу"),
        BotCommand(command="help", description="Помощь и описание"),
        BotCommand(command="balance", description="Показать баланс"),
        BotCommand(command="topup", description="Пополнить баланс"),
        BotCommand(command="referral", description="Пригласить друга"),
        BotCommand(command="feedback", description="Оставить отзыв"),
    ]
    if str(user_id) in admin_ids:
        logger.info("User is an admin, adding admin commands.")
        base_commands.append(BotCommand(command="addshorts", description="Добавить шортсы пользователю"))
        base_commands.append(BotCommand(command="setbalance", description="Установить баланс пользователю"))
        base_commands.append(BotCommand(command="broadcast", description="Сделать рассылку"))
        base_commands.append(BotCommand(command="broadcast_to", description="Сделать рассылку определенным юзерам"))
        base_commands.append(BotCommand(command="start_discount", description="Начать скидку"))
        base_commands.append(BotCommand(command="end_discount", description="Завершить скидку"))
        base_commands.append(BotCommand(command="rm_user", description="Удалить пользователя"))
        base_commands.append(BotCommand(command="export_users", description="Выгрузить пользователей"))
    
    await context.bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=user_id))
    await context.bot.set_my_commands(base_commands, scope=BotCommandScopeChat(chat_id=user_id))



    context.user_data.clear()
    context.user_data['config'] = {}
    context.user_data['balance'] = balance
    
    await update.message.reply_text(
        f"Привет!\nПришлите мне ссылку на YouTube видео, и я сделаю из него короткие виральные ролики для YT Shorts/Reels/Tiktok ⚡️\n\nВаш баланс: {balance} шортсов.\n\n👉 <a href='{TUTORIAL_LINK}'>Инструкция (1 мин. чтения)</a>",
        parse_mode="HTML"
    )
    return GET_URL

async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends the user their referral link."""
    user_id = update.effective_user.id
    bot_username = context.bot.username
    referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    
    await update.message.reply_text(
        "Пригласите друга и получите по 10 шортсов каждый!\n\n" 
        "Отправьте эту ссылку другу:\n" 
        f"`{referral_link}`",
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет сообщение с помощью и списком команд."""
    help_text = (
        "Этот бот создает короткие вирусные видео из YouTube роликов.\n\n" 
        "Доступные команды:\n"
        "/start - Сгенерировать шортс\n"
        "/help - Показать это сообщение\n"
        "/balance - Показать текущий баланс\n"
        "/topup - Пополнить баланс\n"
        "/referral - Пригласить друга\n"
        "/feedback - Оставить отзыв\n\n"
        "@sf_tsupport_bot - по любым вопросам\n\n"
        f"👉 <a href='{TUTORIAL_LINK}'>Инструкция (1 мин. чтения)</a>"
    )
    await update.message.reply_text(help_text, parse_mode="HTML")

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

async def broadcast_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the targeted broadcast conversation."""
    admin_ids_str = os.environ.get("ADMIN_USER_IDS", "")
    admin_ids = [id.strip() for id in admin_ids_str.split(',')]
    if str(update.effective_user.id) not in admin_ids:
        return ConversationHandler.END

    if not context.args:
        await update.message.reply_text("Пожалуйста, укажите ID пользователей через запятую. Например: /broadcast_to 123,456,789")
        return ConversationHandler.END

    try:
        user_ids_str = " ".join(context.args)
        user_ids = [int(uid.strip()) for uid in user_ids_str.split(',')]
        context.user_data['broadcast_to_ids'] = user_ids
        await update.message.reply_text(f"Готовлю рассылку для {len(user_ids)} пользователей. Отправьте пост, который нужно разослать. Для отмены - /cancel.")
        return GET_TARGETED_BROADCAST_MESSAGE
    except (ValueError, IndexError):
        await update.message.reply_text("Неверный формат ID. Пожалуйста, укажите ID пользователей через запятую. Например: /broadcast_to 123,456,789")
        return ConversationHandler.END

async def broadcast_to_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Sends a broadcast message to a specific list of users."""
    text = update.message.text
    entities = update.message.entities
    caption = update.message.caption
    caption_entities = update.message.caption_entities
    photo = update.message.photo[-1].file_id if update.message.photo else None
    animation = update.message.animation.file_id if update.message.animation else None

    user_ids = context.user_data.get('broadcast_to_ids', [])
    if not user_ids:
        await update.message.reply_text("Не найдены ID пользователей для рассылки.")
        return ConversationHandler.END

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
        "Действие отменено. Все команды – /help"
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

async def remove_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Removes a user from the database."""
    admin_ids_str = os.environ.get("ADMIN_USER_IDS", "")
    admin_ids = [id.strip() for id in admin_ids_str.split(',')]
    if str(update.effective_user.id) not in admin_ids:
        return

    try:
        user_id_str = context.args[0]
        user_id = int(user_id_str)

        delete_user(user_id)

        await update.message.reply_text(f"Пользователь {user_id} успешно удален.")

    except (ValueError, IndexError):
        await update.message.reply_text("Неверный формат команды. Используйте: /rm_user <user_id>")

async def export_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exports all users to a CSV file (admin only)."""
    admin_ids_str = os.environ.get("ADMIN_USER_IDS", "")
    admin_ids = [id.strip() for id in admin_ids_str.split(',')]
    if str(update.effective_user.id) not in admin_ids:
        await update.message.reply_text("This command is for admins only.")
        return

    await update.message.reply_text("Выгружаю данные... Это может занять несколько секунд.")

    try:
        users_data = get_all_users_data()
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow(['user_id', 'balance', 'generated_shorts_count', 'referred_by', 'source'])
        
        # Write data
        writer.writerows(users_data)
        
        output.seek(0)
        
        # Send the file
        await update.message.reply_document(
            document=io.BytesIO(output.getvalue().encode('utf-8')),
            filename=f"users_export_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.csv"
        )

    except Exception as e:
        await update.message.reply_text(f"Произошла ошибка при выгрузке данных: {e}")

async def start_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the feedback conversation."""
    await update.message.reply_text("Отправьте текст вашего отзыва. Для отмены - /cancel")
    return GET_FEEDBACK_TEXT