import logging
import asyncio
import yookassa
from yookassa import Configuration
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import ContextTypes, ConversationHandler
import uuid
from datetime import datetime, timezone
from aiocryptopay import AioCryptoPay, Networks

from database import get_user, add_to_user_balance, get_user_referrer, has_referral_discount, set_referral_discount
from pricing import get_package_prices
from analytics import log_event
from states import GET_TOPUP_METHOD, GET_YOOKASSA_EMAIL, YOOKASSA_PAYMENT, CRYPTO_PAYMENT
from localization import get_translation
from config import (
    CRYPTO_BOT_TOKEN, YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, REFERRER_REWARD
)

logger = logging.getLogger(__name__)

async def broadcast_topup_package_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves the selected package from a broadcast and prompts for the payment method."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    _, _, _, lang, _ = get_user(user_id)
    context.user_data['lang'] = lang

    package_data = query.data.split('_')
    generations = int(float(package_data[-1]))

    # Get the current prices
    discount_active = context.bot_data.get('discount_active', False)
    discount_end_time = context.bot_data.get('discount_end_time')
    is_discount_time = discount_active and discount_end_time and datetime.now(timezone.utc) < discount_end_time
    packages = get_package_prices(discount_active=is_discount_time)

    # Find the selected package
    selected_package = next((p for p in packages if p['generations'] == generations), None)

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
    generations = int(package_data[2])

    # Get the current prices
    discount_active = context.bot_data.get('discount_active', False)
    discount_end_time = context.bot_data.get('discount_end_time')
    is_discount_time = discount_active and discount_end_time and datetime.now(timezone.utc) < discount_end_time

    referral_discount_active = has_referral_discount(user_id)

    packages = get_package_prices(discount_active=is_discount_time, referral_discount_active=referral_discount_active)

    # Find the selected package
    selected_package = next((p for p in packages if p['generations'] == generations), None)

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

    amount = package['generations']
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
            "description": f"Top-up for {amount} generations",
            "metadata": {
                "user_id": user_id,
                "generations_amount": amount
            },
            "receipt": {
                "customer": {
                    "email": email
                },
                "items": [
                    {
                        "description": f"Top-up for {amount} generations",
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
            if has_referral_discount(user_id_from_payload):
                set_referral_discount(user_id_from_payload, False) # Consume the discount
                referrer_id = get_user_referrer(user_id_from_payload)
                if referrer_id:
                    add_to_user_balance(referrer_id, REFERRER_REWARD)
                    _, _, _, referrer_lang, _ = get_user(referrer_id)
                    try:
                        referred_user_chat = await context.bot.get_chat(user_id_from_payload)
                        await context.bot.send_message(
                            chat_id=referrer_id,
                            text=get_translation(referrer_lang, "friend_topped_up_balance").format(
                                bonus_amount=REFERRER_REWARD,
                                new_user_mention=referred_user_chat.mention_html()
                            ),
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"Failed to send referral reward notification to {referrer_id}: {e}")
            _, new_balance, _, _, _ = get_user(user_id_from_payload)
            log_event(user_id_from_payload, 'payment_success', {'provider': 'yookassa', 'generations_amount': amount, 'total_amount': float(payment.amount.value), 'currency': payment.amount.currency})

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

    generations_amount = package['generations']
    stars_amount = package['stars']
    
    chat_id = update.effective_chat.id
    title = get_translation(lang, "topup_invoice_title").format(generations_amount=generations_amount)
    description = get_translation(lang, "topup_invoice_description").format(generations_amount=generations_amount)
    payload = f"topup-{chat_id}-{generations_amount}-{stars_amount}"
    currency = "XTR"
    prices = [LabeledPrice(get_translation(lang, "n_generations").format(generations_amount=generations_amount), stars_amount)]

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

    amount = package['generations']
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
        get_translation(lang, "you_are_buying_n_generations_for_m_usdt").format(amount=amount, total_price=total_price),
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
    generations_amount = int(payload_parts[2])

    add_to_user_balance(user_id, generations_amount)
    if has_referral_discount(user_id):
        set_referral_discount(user_id, False) # Consume the discount
        referrer_id = get_user_referrer(user_id)
        if referrer_id:
            add_to_user_balance(referrer_id, REFERRER_REWARD)
            _, _, _, referrer_lang, _ = get_user(referrer_id)
            try:
                referred_user_chat = await context.bot.get_chat(user_id)
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=get_translation(referrer_lang, "friend_topped_up_balance").format(
                        bonus_amount=REFERRER_REWARD,
                        new_user_mention=referred_user_chat.mention_html()
                    ),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Failed to send referral reward notification to {referrer_id}: {e}")
    _, new_balance, _, lang, _ = get_user(user_id)

    log_event(user_id, 'payment_success', {'provider': 'telegram_stars', 'generations_amount': generations_amount, 'total_amount': payment_info.total_amount, 'currency': payment_info.currency})

    await context.bot.send_message(
        chat_id=user_id,
        text=get_translation(lang, "payment_successful").format(amount=generations_amount, new_balance=new_balance),
        parse_mode="HTML"
    )

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
                if has_referral_discount(user_id_from_payload):
                    set_referral_discount(user_id_from_payload, False) # Consume the discount
                    referrer_id = get_user_referrer(user_id_from_payload)
                    if referrer_id:
                        add_to_user_balance(referrer_id, REFERRER_REWARD)
                        _, _, _, referrer_lang, _ = get_user(referrer_id)
                        try:
                            referred_user_chat = await context.bot.get_chat(user_id_from_payload)
                            await context.bot.send_message(
                                chat_id=referrer_id,
                                text=get_translation(referrer_lang, "friend_topped_up_balance").format(
                                    bonus_amount=REFERRER_REWARD,
                                    new_user_mention=referred_user_chat.mention_html()
                                ),
                                parse_mode="HTML"
                                
                            )
                        except Exception as e:
                            logger.error(f"Failed to send referral reward notification to {referrer_id}: {e}")
                _, new_balance, _, _, _ = get_user(user_id_from_payload)
                log_event(user_id_from_payload, 'payment_success', {'provider': 'cryptobot', 'generations_amount': amount, 'total_amount': invoices[0].amount, 'currency': invoices[0].asset})

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
