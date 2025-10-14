import logging
import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeChat
from telegram.ext import ContextTypes, ConversationHandler
from telegram.error import TelegramError
from database import get_user, add_to_user_balance, set_user_balance, get_all_user_ids, delete_user, set_user_language
from analytics import log_event
from states import GET_URL, GET_TOPUP_METHOD, GET_BROADCAST_MESSAGE, GET_FEEDBACK_TEXT, GET_TARGETED_BROADCAST_MESSAGE, GET_LANGUAGE, GET_TOPUP_PACKAGE
from config import TUTORIAL_LINK, ADMIN_USER_IDS
from datetime import datetime, timezone
from localization import get_translation
from pricing import get_package_prices

import csv
import io
from database import get_all_users_data

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def is_admin(user_id: int) -> bool:
    """Checks if a user is an admin."""
    return str(user_id) in ADMIN_USER_IDS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало диалога, запрашивает URL."""
    user_id = update.effective_user.id
    message = update.message or update.callback_query.message
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

    _, balance, _, lang, is_new = get_user(user_id, referrer_id=referrer_id, source=source)

    if is_new:
        lang = 'ru'
        set_user_language(user_id, lang)
        log_event(user_id, 'new_user', {'username': update.effective_user.username, 'referrer_id': referrer_id, 'source': source})
        if referrer_id and referrer_id != user_id:
            # Award bonuses
            add_to_user_balance(user_id, 10)
            add_to_user_balance(referrer_id, 10)
            
            # Update local balance for the new user
            balance += 10
            
            await message.reply_text(get_translation(lang, "welcome_referral_bonus"))
            
            try:
                # Try to get the new user's username to mention them
                new_user_mention = f"@{update.effective_user.username}" if update.effective_user.username else f"user {user_id}"
                _, _, _, referrer_lang, _ = get_user(referrer_id)
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=get_translation(referrer_lang, "friend_joined_referral_bonus").format(new_user_mention=new_user_mention)
                )
            except Exception as e:
                logger.error(f"Failed to send referral notification to {referrer_id}: {e}")

    # Set commands for the user
    base_commands = [
        BotCommand(command="start", description=get_translation(lang, "generate_video_button")),
        BotCommand(command="menu", description=get_translation(lang, "help_description")),
        BotCommand(command="topup", description=get_translation(lang, "topup_description")),
        BotCommand(command="referral", description=get_translation(lang, "referral_description")),
        BotCommand(command="feedback", description=get_translation(lang, "feedback_description")),
        BotCommand(command="lang", description=get_translation(lang, "language_description")),
    ]
    if is_admin(user_id):
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
    
    if is_new:
        keyboard = [
            [InlineKeyboardButton(get_translation(lang, "demo_button"), callback_data='start_demo')],
            [InlineKeyboardButton(get_translation(lang, "how_it_works_button"), url=TUTORIAL_LINK)]
        ]
    else:
        keyboard = [
            [InlineKeyboardButton(get_translation(lang, "how_it_works_button"), url=TUTORIAL_LINK)]
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await message.reply_text(
        get_translation(lang, "start_message") + ("\n\n<b>Switch language – /lang</b>" if is_new else ""),
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    return GET_URL

async def lang_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Shows language selection."""
    keyboard = [
        [InlineKeyboardButton("🇷🇺 Русский", callback_data='set_lang_ru')],
        [InlineKeyboardButton("🇬🇧 English", callback_data='set_lang_en')]
        
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Please select your language:", reply_markup=reply_markup)
    return GET_LANGUAGE

async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Sets the user language."""
    query = update.callback_query
    await query.answer()
    lang = query.data.split('_')[-1]
    user_id = query.from_user.id
    set_user_language(user_id, lang)
    
    await query.edit_message_text(get_translation(lang, "language_set"))
    
    # Restart the conversation to apply the new language
    return await start(update, context)

async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends the user their referral link."""
    user_id = update.effective_user.id
    _, _, _, lang, _ = get_user(user_id)
    bot_username = context.bot.username
    referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    
    await update.message.reply_text(
        get_translation(lang, "referral_message").format(referral_link=referral_link),
        parse_mode="Markdown"
    )

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет сообщение с помощью и списком команд."""
    user_id = update.effective_user.id
    _, _, _, lang, _ = get_user(user_id)
    help_text = get_translation(lang, "help_text").format(tutorial_link=TUTORIAL_LINK)
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(get_translation(lang, "how_it_works_button"), url=TUTORIAL_LINK)]
    ])
    await update.message.reply_text(help_text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)

from pricing import get_package_prices

async def topup_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Shows the available packages for top-up with prices in RUB."""
    query = update.callback_query
    if query:
        await query.answer()
    
    user_id = update.effective_user.id
    _, balance, _, lang, _ = get_user(user_id)
    log_event(user_id, 'topup_start', {})

    bot_username = context.bot.username
    referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"

    discount_active = context.bot_data.get('discount_active', False)
    discount_end_time = context.bot_data.get('discount_end_time')

    is_discount_time = discount_active and discount_end_time and datetime.now(timezone.utc) < discount_end_time

    packages = get_package_prices(discount_active=is_discount_time)
    
    keyboard = []
    if is_discount_time:
        message_text = get_translation(lang, "topup_package_prompt_discount").format(balance=balance)
        for package in packages:
            shorts = package['shorts']
            rub = package['rub']
            original_rub = package['original_rub']
            stars = package['stars']
            usdt = package['usdt']
            if package['highlight']:
                button_text = "🔥 " + get_translation(lang, "n_shorts_rub_discount_button").format(shorts=shorts, old_rub=original_rub, new_rub=rub) + " 🔥"
            else:
                button_text = get_translation(lang, "n_shorts_rub_discount_button").format(shorts=shorts, old_rub=original_rub, new_rub=rub)
            button = InlineKeyboardButton(button_text, callback_data=f'topup_package_{shorts}_{rub}_{stars}_{usdt}')
            keyboard.append([button])
    else:
        message_text = get_translation(lang, "topup_package_prompt").format(balance=balance)
        for package in packages:
            shorts = package['shorts']
            rub = package['rub']
            stars = package['stars']
            usdt = package['usdt']
            if package['highlight']:
                button_text = "🔥 " + get_translation(lang, "n_shorts_rub_button").format(shorts=shorts, rub=rub) + " 🔥"
            else:
                button_text = get_translation(lang, "n_shorts_rub_button").format(shorts=shorts, rub=rub)
            button = InlineKeyboardButton(button_text, callback_data=f'topup_package_{shorts}_{rub}_{stars}_{usdt}')
            keyboard.append([button])
    
    message_text += "\n\n" + get_translation(lang, "referral_message").format(referral_link=referral_link)

    keyboard.append([InlineKeyboardButton(get_translation(lang, "cancel_button"), callback_data='cancel_topup')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        await query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode="Markdown")
        
    return GET_TOPUP_PACKAGE

async def add_shorts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Adds a specified amount of shorts to a user's balance."""
    if not is_admin(update.effective_user.id):
        return

    try:
        user_id_str, amount_str = context.args
        user_id = int(user_id_str)
        amount = int(amount_str)

        if amount <= 0:
            await update.message.reply_text("Количество шортсов должно быть положительным числом.")
            return

        add_to_user_balance(user_id, amount)
        _, new_balance, _, _, _ = get_user(user_id)

        await update.message.reply_text(f"Баланс пользователя {user_id} успешно пополнен на {amount} шортсов. Новый баланс: {new_balance}.")

    except (ValueError, IndexError):
        await update.message.reply_text("Неверный формат команды. Используйте: /addshorts <user_id> <amount>")

async def set_user_balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sets a user's balance to a specified amount."""
    if not is_admin(update.effective_user.id):
        return

    try:
        if len(context.args) != 2:
            await update.message.reply_text("Неверный формат команды. Используйте: /setbalance <user_id1,user_id2,...> <amount>")
            return

        user_ids_str = context.args[0]
        amount_str = context.args[1]
        
        user_ids = [int(uid.strip()) for uid in user_ids_str.split(',')]
        amount = int(amount_str)

        if amount < 0:
            await update.message.reply_text("Баланс не может быть отрицательным.")
            return

        success_users = []
        failed_users = []

        for user_id in user_ids:
            try:
                set_user_balance(user_id, amount)
                success_users.append(str(user_id))
            except Exception as e:
                logger.error(f"Failed to set balance for user {user_id}: {e}")
                failed_users.append(str(user_id))
        
        response_parts = []
        if success_users:
            response_parts.append(f"Баланс успешно установлен в {amount} для пользователей: {', '.join(success_users)}.")
        
        if failed_users:
            response_parts.append(f"Не удалось установить баланс для пользователей: {', '.join(failed_users)}.")

        await update.message.reply_text("\n".join(response_parts))

    except (ValueError, IndexError):
        await update.message.reply_text("Неверный формат команды. Используйте: /setbalance <user_id1,user_id2,...> <amount>")


async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the broadcast conversation."""
    if not is_admin(update.effective_user.id):
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
    if not is_admin(update.effective_user.id):
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
    user_id = update.effective_user.id
    _, _, _, lang, _ = get_user(user_id)
    context.user_data.clear()
    context.user_data['config'] = {}
    await update.message.reply_text(
        get_translation(lang, "action_cancelled")
    )
    return ConversationHandler.END

async def start_discount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Starts a discount period."""
    if not is_admin(update.effective_user.id):
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
    if not is_admin(update.effective_user.id):
        return

    context.bot_data['discount_active'] = False
    context.bot_data.pop('discount_end_time', None)
    
    await update.message.reply_text("✅ Скидка завершена.")

async def remove_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Removes a user from the database."""
    if not is_admin(update.effective_user.id):
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
    if not is_admin(update.effective_user.id):
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
    user_id = update.effective_user.id
    _, _, _, lang, _ = get_user(user_id)
    await update.message.reply_text(get_translation(lang, "send_feedback_prompt"))
    return GET_FEEDBACK_TEXT