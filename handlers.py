import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import ContextTypes, ConversationHandler
import uuid
from telegram.error import TimedOut
from database import get_user, update_user_balance, add_to_user_balance
import os
import asyncio
from processing.bot_logic import main as process_video
from utils import format_config
from analytics import log_event
from states import (
    GET_URL,
    GET_SUBTITLE_STYLE,
    GET_BOTTOM_VIDEO,
    GET_LAYOUT,
    GET_SUBTITLES_TYPE,
    CONFIRM_CONFIG,
    GET_SHORTS_NUMBER,
    GET_TOPUP_METHOD,
    GET_TOPUP_PACKAGE,
    GET_CRYPTO_AMOUNT,
    CRYPTO_PAYMENT,
    RATING,
    FEEDBACK,
    PROCESSING
)
from datetime import datetime, timezone
from config import REGULAR_PRICES, DISCOUNT_PRICES, FEEDBACK_GROUP_ID

logger = logging.getLogger(__name__)

path_to_config_examples = "config_examples/"

async def get_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет URL и запрашивает количество шортсов."""
    balance = context.user_data.get('balance', 0)
    if balance <= 0:
        topup_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Пополнить баланс", callback_data='topup_start')]
        ])
        await update.message.reply_text(
            "У вас закончились шортсы. Пожалуйста, пополните баланс.",
            reply_markup=topup_keyboard
        )
        return ConversationHandler.END

    url = update.message.text
    if "youtube.com/" not in url and "youtu.be/" not in url:
        await update.message.reply_text("Пожалуйста, пришлите корректную ссылку на YouTube видео.")
        return GET_URL

    context.user_data['url'] = url
    logger.info(f"Пользователь {update.effective_user.id} предоставил URL: {url}")

    # Set default transcription method
    context.user_data['config']['force_ai_transcription'] = False
    logger.info(f"Config for {update.effective_user.id}: force_ai_transcription = False (default)")

    keyboard = [
        [InlineKeyboardButton("Авто", callback_data='auto')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message = await update.message.reply_text(
        "Сколько шортсов мне нужно сделать? Отправьте число или нажмите \"Авто\"",
        reply_markup=reply_markup
    )
    context.user_data['shorts_number_message_id'] = message.message_id
    return GET_SHORTS_NUMBER


async def get_shorts_number_auto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет 'Авто' для количества шортсов и запрашивает сетку."""
    query = update.callback_query
    await query.answer()
    context.user_data['config']['shorts_number'] = 'auto'
    logger.info(f"Config for {query.from_user.id}: shorts_number = 'auto'")

    keyboard = [
        [
            InlineKeyboardButton("1:1", callback_data='square_center'),
            InlineKeyboardButton("1:1 + brainrot", callback_data='square_top_brainrot_bottom'),
        ],
        [
            InlineKeyboardButton("16:9", callback_data='full_center'),
            InlineKeyboardButton("16:9 + brainrot", callback_data='full_top_brainrot_bottom'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.delete()
    await context.bot.send_photo(
        chat_id=query.message.chat_id,
        photo=open(path_to_config_examples + 'layout_examples.png', 'rb'),
        caption="Выберите сетку шортса:",
        reply_markup=reply_markup
    )
    return GET_LAYOUT


async def get_shorts_number_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет число шортсов и запрашивает сетку."""
    
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
        keyboard = [[InlineKeyboardButton("Авто", callback_data='auto')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Сколько шортсов мне нужно сделать? Отправьте число или нажмите \"Авто\"",
            reply_markup=reply_markup
        )
        context.user_data['shorts_number_message_id'] = message.message_id

    try:
        number = int(update.message.text)
        balance = context.user_data.get('balance', 0)

        if number <= 0:
            msg = await context.bot.send_message(chat_id=update.effective_chat.id, text="Пожалуйста, введите положительное число.")
            context.user_data['error_message_id'] = msg.message_id
            await resend_prompt(context)
            return GET_SHORTS_NUMBER
        
        if number > balance:
            topup_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Пополнить баланс", callback_data='topup_start')]
            ])
            msg = await context.bot.send_message(
                chat_id=update.effective_chat.id, 
                text=f"У вас на балансе {balance} шортсов. Пожалуйста, введите число не больше {balance}.",
                reply_markup=topup_keyboard
            )
            context.user_data['error_message_id'] = msg.message_id
            await resend_prompt(context)
            return GET_SHORTS_NUMBER

        context.user_data['config']['shorts_number'] = number
        logger.info(f"Config for {update.effective_user.id}: shorts_number = {number}")

        keyboard = [
            [
                InlineKeyboardButton("1:1", callback_data='square_center'),
                InlineKeyboardButton("1:1 + brainrot", callback_data='square_top_brainrot_bottom'),
            ],
            [
                InlineKeyboardButton("16:9", callback_data='full_center'),
                InlineKeyboardButton("16:9 + brainrot", callback_data='full_top_brainrot_bottom'),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=open(path_to_config_examples + 'layout_examples.png', 'rb'),
            caption="Выберите сетку шортса:",
            reply_markup=reply_markup
        )
        return GET_LAYOUT
    except ValueError:
        msg = await context.bot.send_message(chat_id=update.effective_chat.id, text="Пожалуйста, введите целое число или нажмите кнопку 'Авто'.")
        context.user_data['error_message_id'] = msg.message_id
        await resend_prompt(context)
        return GET_SHORTS_NUMBER

async def get_subtitle_style(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет стиль субтитров и показывает экран подтверждения."""
    query = update.callback_query
    await query.answer()
    context.user_data['config']['subtitle_style'] = query.data
    logger.info(f"Config for {query.from_user.id}: subtitle_style = {query.data}")

    # Set default capitalization
    context.user_data['config']['capitalize_sentences'] = False
    logger.info(f"Config for {query.from_user.id}: capitalize_sentences = False (default)")

    balance = context.user_data.get('balance')
    settings_text = format_config(context.user_data['config'], balance)

    keyboard = [
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data='confirm'),
            InlineKeyboardButton("❌ Отклонить", callback_data='cancel'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"Подтвердите настройки:\n\n{settings_text}",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

    return CONFIRM_CONFIG

async def get_bottom_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет фоновое видео и запрашивает тип субтитров."""
    query = update.callback_query
    await query.answer()
    choice = query.data if query.data != 'none' else None
    context.user_data['config']['bottom_video'] = choice
    logger.info(f"Config for {query.from_user.id}: bottom_video = {choice}")

    keyboard = [
        [
            InlineKeyboardButton("Одно слово", callback_data='word-by-word'),
            InlineKeyboardButton("Фраза", callback_data='phrases'),
        ],
        [InlineKeyboardButton("Без субтитров", callback_data='no_subtitles')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.delete()
    await context.bot.send_photo(
        chat_id=query.message.chat_id,
        photo=open(path_to_config_examples + 'subs_examples.png', 'rb'),
        caption="Выберите, как показывать субтитры:",
        reply_markup=reply_markup
    )
    return GET_SUBTITLES_TYPE

async def get_layout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет расположение и запрашивает фоновое видео (или пропускает)."""
    query = update.callback_query
    await query.answer()
    layout_choice = query.data
    context.user_data['config']['layout'] = layout_choice
    logger.info(f"Config for {query.from_user.id}: layout = {layout_choice}")

    await query.message.delete()

    if layout_choice in ['square_center', 'full_center']:
        context.user_data['config']['bottom_video'] = None
        logger.info(f"Layout for {query.from_user.id} is {layout_choice}, skipping bottom video selection.")
        
        keyboard = [
            [
                InlineKeyboardButton("Одно слово", callback_data='word-by-word'),
                InlineKeyboardButton("Фраза", callback_data='phrases'),
            ],
            [InlineKeyboardButton("Без субтитров", callback_data='no_subtitles')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=open(path_to_config_examples + 'subs_examples.png', 'rb'),
                caption="Выберите, как показывать субтитры:",
                reply_markup=reply_markup
            )
        except TimedOut:
            logger.warning(f"Timeout error sending photo to {query.message.chat_id} in get_layout. Sending text fallback.")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="Не удалось загрузить изображение. Выберите, как показывать субтитры:",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error sending photo in get_layout (if block): {e}", exc_info=True)
            await context.bot.send_message(chat_id=query.message.chat_id, text="Произошла непредвиденная ошибка. Попробуйте начать заново: /start")
            return ConversationHandler.END
            
        return GET_SUBTITLES_TYPE
    else:
        keyboard = [
            [
                InlineKeyboardButton("GTA", callback_data='gta'),
                InlineKeyboardButton("Minecraft", callback_data='minecraft'),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=open(path_to_config_examples + 'brainrot_examples.png', 'rb'),
                caption="Выберите brainrot видео:",
                reply_markup=reply_markup
            )
        except TimedOut:
            logger.warning(f"Timeout error sending photo to {query.message.chat_id} in get_layout. Sending text fallback.")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="Не удалось загрузить изображение. Выберите brainrot видео:",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error sending photo in get_layout (else block): {e}", exc_info=True)
            await context.bot.send_message(chat_id=query.message.chat_id, text="Произошла непредвиденная ошибка. Попробуйте начать заново: /start")
            return ConversationHandler.END

        return GET_BOTTOM_VIDEO

async def get_subtitles_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет тип субтитров и запрашивает стиль субтитров или подтверждение."""
    query = update.callback_query
    await query.answer()
    choice = query.data
    context.user_data['config']['subtitles_type'] = choice
    logger.info(f"Config for {query.from_user.id}: subtitles_type = {choice}")

    if choice == 'no_subtitles':
        context.user_data['config']['subtitle_style'] = None
        logger.info(f"Config for {query.from_user.id}: subtitle_style = None")
        
        balance = context.user_data.get('balance')
        settings_text = format_config(context.user_data['config'], balance)

        keyboard = [
            [
                InlineKeyboardButton("✅ Подтвердить", callback_data='confirm'),
                InlineKeyboardButton("❌ Отклонить", callback_data='cancel'),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.delete()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"<b>Подтвердите настройки:</b>\n\n{settings_text}",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        return CONFIRM_CONFIG
    else:
        keyboard = [
            [InlineKeyboardButton("Белый", callback_data='white'), InlineKeyboardButton("Желтый", callback_data='yellow')],
            [InlineKeyboardButton("Фиолетовый", callback_data='purple'), InlineKeyboardButton("Зелёный", callback_data='green')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.delete()
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=open(path_to_config_examples + 'subs_color_examples.png', 'rb'),
            caption="Выберите цвет субтитров:",
            reply_markup=reply_markup
        )
        return GET_SUBTITLE_STYLE




async def confirm_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Добавляет задачу в очередь после подтверждения."""
    query = update.callback_query
    await query.answer()

    balance = context.user_data.get('balance', 0)
    shorts_number = context.user_data.get('config', {}).get('shorts_number', 'auto')

    if isinstance(shorts_number, int):
        if balance < shorts_number:
            topup_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Пополнить баланс", callback_data='topup_start')]
            ])
            await query.edit_message_text(
                f"На вашем балансе ({balance}) недостаточно шортсов для генерации {shorts_number} видео. Пожалуйста, пополните баланс или выберите меньшее количество.",
                reply_markup=topup_keyboard
            )
            return ConversationHandler.END
    elif shorts_number == 'auto':
        # В режиме "авто" мы не знаем точное количество. 
        # Можно либо списать максимум, либо проверять по факту.
        # Пока что просто пропускаем, если баланс > 0, что уже проверено в start.
        pass

    generation_id = str(uuid.uuid4())
    context.user_data['generation_id'] = generation_id

    processing_queue = context.bot_data['processing_queue']
    task_data = {
        'chat_id': query.message.chat.id,
        'user_data': context.user_data.copy(),
        'status_message_id': query.message.message_id
    }

    event_data = {
        'url': context.user_data['url'],
        'config': context.user_data['config'],
        'generation_id': generation_id,
        'queue_position': processing_queue.qsize() + 1
    }
    log_event(query.message.chat.id, 'generation_queued', event_data)
    
    await processing_queue.put(task_data)
    
    logger.info(f"Задача для чата {query.message.chat.id} добавлена в очередь. Задач в очереди: {processing_queue.qsize()}")

    settings_text = format_config(context.user_data['config'], balance)
    url = context.user_data['url']
    await query.edit_message_text(
        text=f"⏳ Ваш запрос добавлен в очередь (вы <b>#{processing_queue.qsize()} в очереди</b>). Вы получите уведомление, когда обработка начнется.\n\n<b>Ваши настройки:</b>\nURL: {url}\n{settings_text}",
        parse_mode="HTML"
    )

    return PROCESSING

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отменяет текущую конфигурацию и возвращает к началу."""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data['config'] = {}

    # Re-fetch balance
    user_id = query.from_user.id
    _, balance, _, _ = get_user(user_id)
    context.user_data['balance'] = balance
    
    await query.edit_message_text(
        f"Настройки отменены. У вас на балансе {balance} шортсов.\nПришли мне ссылку на YouTube видео, чтобы начать заново."
    )
    return GET_URL

async def back_to_topup_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Goes back to the top-up method selection."""
    query = update.callback_query
    await query.answer()
    keyboard = [
        [
            InlineKeyboardButton("⭐️ Telegram Stars", callback_data='topup_stars'),
            InlineKeyboardButton("💎 CryptoBot", callback_data='topup_crypto'),
        ],
        [InlineKeyboardButton("❌ Отмена", callback_data='cancel_topup')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Выберите способ пополнения:", reply_markup=reply_markup)
    return GET_TOPUP_METHOD


async def topup_stars(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Shows the available packages for Telegram Stars top-up."""
    query = update.callback_query
    await query.answer()

    discount_active = context.bot_data.get('discount_active', False)
    discount_end_time = context.bot_data.get('discount_end_time')

    keyboard = []
    if discount_active and discount_end_time and datetime.now(timezone.utc) < discount_end_time:
        packages = DISCOUNT_PRICES["stars_packages"]
        old_packages = REGULAR_PRICES["stars_packages"]
        message_text = "⭐️ Выберите пакет для пополнения (действует скидка!):"
        for i, new_package in enumerate(packages):
            old_price = old_packages[i]['stars']
            new_price = new_package['stars']
            shorts = new_package['shorts']
            button_text = f"{shorts} шортсов: {old_price} → {new_price} ⭐️"
            button = InlineKeyboardButton(button_text, callback_data=f'topup_{shorts}_{new_price}')
            keyboard.append([button])
    else:
        packages = REGULAR_PRICES["stars_packages"]
        message_text = "Выберите пакет для пополнения через ⭐️ Telegram Stars:"
        for package in packages:
            shorts = package['shorts']
            stars = package['stars']
            button = InlineKeyboardButton(f"{shorts} шортсов: {stars} ⭐️", callback_data=f'topup_{shorts}_{stars}')
            keyboard.append([button])
    
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data='back_to_topup_method')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message_text, reply_markup=reply_markup)
    return GET_TOPUP_PACKAGE

async def send_invoice_for_stars(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Sends an invoice for the selected package."""
    query = update.callback_query
    await query.answer()
    await query.delete_message()
    
    package = query.data.split('_')
    shorts_amount = int(package[1])
    stars_amount = int(package[2])
    
    chat_id = update.effective_chat.id
    title = f"Пополнение баланса на {shorts_amount} шортсов"
    description = f"Пакет '{shorts_amount} шортсов' для генерации видео."
    payload = f"topup-{chat_id}-{shorts_amount}-{stars_amount}"
    currency = "XTR"
    prices = [LabeledPrice(f"{shorts_amount} шортсов", stars_amount)]

    await context.bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description=description,
        payload=payload,
        provider_token=None,  # Not needed for Telegram Stars
        currency=currency,
        prices=prices
    )
    return ConversationHandler.END

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: 
    """Answers the PreCheckoutQuery."""
    query = update.pre_checkout_query
    if query.invoice_payload.startswith('topup-'):
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Что-то пошло не так...")

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: 
    """Confirms the successful payment."""
    payment_info = update.message.successful_payment
    payload_parts = payment_info.invoice_payload.split('-')
    user_id = int(payload_parts[1])
    shorts_amount = int(payload_parts[2])

    add_to_user_balance(user_id, shorts_amount)
    _, new_balance, _, _ = get_user(user_id)

    log_event(user_id, 'payment_success', {'provider': 'telegram_stars', 'shorts_amount': shorts_amount, 'total_amount': payment_info.total_amount, 'currency': payment_info.currency})

    await context.bot.send_message(
        chat_id=user_id,
        text=f"💸 Оплата прошла успешно!\nВаш баланс пополнен на {shorts_amount} шортс.\n\nНовый баланс: <b>{new_balance} шортс.</b>",
        parse_mode="HTML"
    )

async def topup_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Asks the user for the amount of shorts to buy with crypto."""
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_topup_method')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "Введите количество шортсов, которое вы хотите купить:",
        reply_markup=reply_markup
    )
    return GET_CRYPTO_AMOUNT

from aiocryptopay import AioCryptoPay, Networks


async def get_crypto_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the amount of shorts to buy with crypto."""
    try:
        amount = int(update.message.text)
        if amount <= 0:
            await update.message.reply_text("Пожалуйста, введите положительное число.")
            return GET_CRYPTO_AMOUNT

        discount_active = context.bot_data.get('discount_active', False)
        discount_end_time = context.bot_data.get('discount_end_time')

        if discount_active and discount_end_time and datetime.now(timezone.utc) < discount_end_time:
            price_per_short = DISCOUNT_PRICES["crypto_price_per_short"]
            discounts = DISCOUNT_PRICES["crypto_discounts"]
        else:
            price_per_short = REGULAR_PRICES["crypto_price_per_short"]
            discounts = REGULAR_PRICES["crypto_discounts"]

        # Tiered pricing logic
        discount = 0
        for threshold, discount_value in sorted(discounts.items(), reverse=True):
            if amount >= threshold:
                discount = discount_value
                break

        final_price_per_short = price_per_short * (1 - discount)
        total_price = round(amount * final_price_per_short, 2)

        # --- CryptoBot Integration (Real) ---
        crypto = AioCryptoPay(token=os.environ.get("CRYPTO_BOT_TOKEN"), network=Networks.MAIN_NET)
        invoice = await crypto.create_invoice(asset='USDT', amount=total_price)
        await crypto.close()

        payment_url = invoice.bot_invoice_url
        invoice_id = invoice.invoice_id

        payload = f"check_crypto:{update.effective_user.id}:{amount}:{invoice_id}"

        keyboard = [
            [InlineKeyboardButton("Оплатить", url=payment_url)],
            [InlineKeyboardButton("Проверить платёж", callback_data=payload)],
            [InlineKeyboardButton("❌ Отмена", callback_data='back_to_topup_method')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"Вы покупаете {amount} шортсов за {total_price} USDT. Нажмите кнопку ниже, чтобы оплатить.",
            reply_markup=reply_markup
        )
        
        return CRYPTO_PAYMENT

    except ValueError:
        await update.message.reply_text("Пожалуйста, введите целое число.")
        return GET_CRYPTO_AMOUNT

async def check_crypto_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Checks the crypto payment and updates the balance."""
    query = update.callback_query
    await query.answer()

    try:
        identifier, user_id_str, amount_str, invoice_id = query.data.split(':')
        user_id = int(user_id_str)
        amount = int(amount_str)

        try:
            # --- CryptoBot Integration (Real) ---
            crypto = AioCryptoPay(token=os.environ.get("CRYPTO_BOT_TOKEN"), network=Networks.MAIN_NET)
            invoices = await crypto.get_invoices(invoice_ids=invoice_id)
            await crypto.close()

            if invoices and invoices[0].status == 'paid':
                # Delete previous "payment not found" messages
                if 'payment_not_found_messages' in context.user_data:
                    for message_id in context.user_data['payment_not_found_messages']:
                        try:
                            await context.bot.delete_message(chat_id=user_id, message_id=message_id)
                        except Exception as e:
                            logger.warning(f"Could not delete message {message_id}: {e}")
                    del context.user_data['payment_not_found_messages']

                add_to_user_balance(user_id, amount)
                _, new_balance, _, _ = get_user(user_id)
                log_event(user_id, 'payment_success', {'provider': 'cryptobot', 'shorts_amount': amount, 'total_amount': invoices[0].amount, 'currency': invoices[0].asset})

                await query.edit_message_text(
                    f"💸 Оплата прошла успешно!\nВаш баланс пополнен на {amount} шортсов.\n\nНовый баланс: <b>{new_balance} шортсов.</b>",
                    parse_mode="HTML"
                )
                return ConversationHandler.END
            else:
                msg = await context.bot.send_message(chat_id=user_id, text="Платёж не найден или еще не прошел. Попробуйте проверить еще раз через несколько секунд.")
                if 'payment_not_found_messages' not in context.user_data:
                    context.user_data['payment_not_found_messages'] = []
                context.user_data['payment_not_found_messages'].append(msg.message_id)
                return CRYPTO_PAYMENT

        except Exception as e:
            logger.error(f"Error checking crypto payment with aiocryptopay: {e}", exc_info=True)
            await query.edit_message_text("Произошла ошибка при связи с платежной системой. Пожалуйста, попробуйте еще раз позже.")
            return CRYPTO_PAYMENT

    except (ValueError, IndexError) as e:
        logger.error(f"Error checking crypto payment: {e}", exc_info=True)
        await query.edit_message_text("Произошла ошибка при проверке платежа. Пожалуйста, попробуйте еще раз.")
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
    rating = query.data.split('_')[1]
    
    rating_id = str(uuid.uuid4())
    generation_id = context.user_data.get('generation_id')
    log_event(query.from_user.id, 'rating', {'rating_id': rating_id, 'rating': rating, 'generation_id': generation_id})
    
    context.user_data['rating_id'] = rating_id
    context.user_data['rating'] = rating

    keyboard = [[InlineKeyboardButton("Пропустить", callback_data='skip_feedback')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text="Спасибо за оценку! Оставьте, пожалуйста, текстовый отзыв, чтобы мы могли стать лучше.",
        reply_markup=reply_markup
    )
    return FEEDBACK

async def handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the user's text feedback and forwards it."""
    user_id = update.message.from_user.id
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
                    text=f"Пользователь {user_id} поставил оценку: {rating}"
                )
        except Exception as e:
            logger.error(f"Failed to forward feedback to group {FEEDBACK_GROUP_ID}: {e}")

    await update.message.reply_text("Спасибо за ваш отзыв!")
    context.user_data.clear()
    return ConversationHandler.END

async def handle_user_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the user's text feedback and forwards it."""
    user_id = update.message.from_user.id

    if FEEDBACK_GROUP_ID:
        try:
            await context.bot.forward_message(
                chat_id=FEEDBACK_GROUP_ID,
                from_chat_id=update.message.chat_id,
                message_id=update.message.message_id
            )
            await context.bot.send_message(
                chat_id=FEEDBACK_GROUP_ID,
                text=f"Отзыв от пользователя {user_id}."
            )
        except Exception as e:
            logger.error(f"Failed to forward feedback to group {FEEDBACK_GROUP_ID}: {e}")

    await update.message.reply_text("Спасибо за ваш отзыв!")
    return ConversationHandler.END

async def skip_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Skips the text feedback step."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Спасибо за вашу оценку!")
    context.user_data.clear()
    return ConversationHandler.END