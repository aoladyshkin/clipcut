import os
import logging
import asyncio
import traceback
import html
import json

from telegram import Update, Bot, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, PreCheckoutQueryHandler, CallbackQueryHandler, ContextTypes, ConversationHandler, PicklePersistence
import telegram.error

from conversation import get_conv_handler
from commands import (
    menu_command, add_generations_command, set_user_balance_command, 
    start_discount, end_discount, referral_command, remove_user_command, 
    export_users_command, lang_command, set_language, cancel, start, status_command
)
from handlers import (
    precheckout_callback, successful_payment_callback, handle_dislike_button, 
    handle_moderation_button, handle_feedback_approval, broadcast_topup_package_selection,
    topup_stars, topup_crypto, topup_yookassa, get_yookassa_email, check_yookassa_payment,
    check_crypto_payment, back_to_package_selection, cancel_topup
)
from processing.bot_logic import main as process_video
from states import RATING, GET_LANGUAGE, GET_TOPUP_METHOD, GET_YOOKASSA_EMAIL, CRYPTO_PAYMENT, YOOKASSA_PAYMENT
from analytics import init_analytics_db, log_event
from config import (
    TELEGRAM_BOT_TOKEN, MAX_CONCURRENT_TASKS, FORWARD_RESULTS_GROUP_ID, 
    DELETE_OUTPUT_AFTER_SENDING, ADMIN_GROUP_ID, ADMIN_USER_TAG
)
from localization import get_translation
from database import get_user, add_task_to_queue, get_pending_tasks, remove_task_from_queue

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a telegram message to notify the admin."""
    logger.error("Exception while handling an update:", exc_info=context.error)

    if not ADMIN_GROUP_ID:
        return

    # Format the traceback
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)

    # Prepare the message text
    message = (
        f"An exception was raised while handling an update\n"
        f"<pre>{html.escape(str(context.error))}</pre>\n"
        f"Traceback:\n<pre>{html.escape(tb_string)}</pre>"
    )

    # Send the message
    await context.bot.send_message(
        chat_id=ADMIN_GROUP_ID,
        text=f"{ADMIN_USER_TAG}\n{message}",
        parse_mode="HTML"
    )

async def send_message_safely(bot: Bot, chat_id: int, text: str, **kwargs):
    try:
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except telegram.error.BadRequest as e:
        if "Message to be replied not found" in str(e):
            logger.warning(f"Original message not found for chat_id {chat_id}. Sending without reply.")
            kwargs.pop("reply_to_message_id", None)
            return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
        else:
            raise

async def send_status_update(bot: Bot, chat_id: int, text: str, status_message_id: int, edit_message_id: int):
    try:
        await bot.send_message(
            text=text,
            chat_id=chat_id,
            reply_to_message_id=status_message_id,
        )
    except Exception as e:
        logger.warning(f"Не удалось отредактировать сообщение о статусе: {e}. Отправляю новое.")
        await bot.send_message(chat_id=chat_id, text=text, reply_to_message_id=edit_message_id)

async def send_video(bot: Bot, chat_id: int, file_path: str, caption: str, edit_message_id: int, forward_group_id: str = None, generation_id: str = None):
    try:
        _, _, _, lang, _ = get_user(chat_id)
        # dislike_keyboard = InlineKeyboardMarkup([
        #     [InlineKeyboardButton(get_translation(lang, "dislike_button"), callback_data='dislike')]
        # ])

        with open(file_path, 'rb') as video_file:
            message = await bot.send_video(
                chat_id=chat_id,
                video=video_file,
                caption=caption,
                parse_mode="HTML",
                width=720,
                height=1280,
                supports_streaming=True,
                read_timeout=600,
                write_timeout=600,
                reply_to_message_id=edit_message_id,
                # reply_markup=dislike_keyboard
            )

        if forward_group_id:
            try:
                fwd_message = await bot.forward_message(
                    chat_id=forward_group_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id
                )
                logger.info(f"Видео для чата {chat_id} переслано в группу {forward_group_id}")

                if generation_id:
                    await bot.send_message(
                        chat_id=forward_group_id,
                        text=f"Generation ID: `{generation_id}`\nChat ID: `{chat_id}`",
                        reply_to_message_id=fwd_message.message_id,
                        parse_mode="Markdown"
                    )
            except Exception as e:
                logger.error(f"Не удалось переслать видео или отправить generation_id в группу {forward_group_id}: {e}")

        return True
    except Exception as e:
        logger.error(f"Ошибка при отправке видео {file_path} в чат {chat_id}: {e}")
        log_event(chat_id, 'send_video_error', {'file_path': file_path, 'error': str(e)})
        _, _, _, lang, _ = get_user(chat_id)
        await bot.send_message(chat_id, get_translation(lang, "send_video_error").format(file_path=file_path, e=e), reply_to_message_id=edit_message_id)
        return False



async def processing_worker(queue: asyncio.Queue, application: Application):
    """Воркер, который обрабатывает видео из очереди."""
    while True:
        task_id, user_id, chat_id, user_data_json, status_message_id = await queue.get()
        user_data = json.loads(user_data_json)
        
        try:
            async with application.bot_data['busy_workers_lock']:
                application.bot_data['busy_workers'] += 1
            logger.info(f"Начинаю обработку задачи {task_id} для чата {chat_id}")
            await run_processing(chat_id, user_data, application, status_message_id)
            logger.info(f"Задача {task_id} успешно обработана.")
        except Exception as e:
            logger.error(f"Ошибка в воркере для чата {chat_id}: {e}", exc_info=True)
            try:
                _, _, _, lang, _ = get_user(chat_id)
                await application.bot.send_message(
                    chat_id,
                    get_translation(lang, "processing_error").format(e=e),
                    parse_mode="HTML"
                )
            except Exception as send_e:
                logger.error(f"Не удалось отправить сообщение об ошибке в чат {chat_id}: {send_e}")
        finally:
            remove_task_from_queue(task_id) # This line removes the task
            async with application.bot_data['busy_workers_lock']:
                application.bot_data['busy_workers'] -= 1
            queue.task_done()
            logger.info(f"Завершена обработка задачи для чата {chat_id}. Задач в очереди: {queue.qsize()}")


async def run_processing(chat_id: int, user_data: dict, application: Application, status_message_id: int = None):
    """Асинхронно запускает обработку видео и отправляет результат."""
    bot = application.bot
    from database import get_user # Локальный импорт для избежания циклических зависимостей

    generation_id = user_data.get('generation_id')
    log_event(chat_id, 'generation_start', {'generation_id': generation_id})

    _, current_balance, _, lang, _ = get_user(chat_id)

    processing_message = await send_message_safely(
        bot,
        chat_id,
        get_translation(lang, "processing_started"),
        reply_to_message_id=status_message_id
    )
    edit_message_id = processing_message.message_id if processing_message else None

    main_loop = asyncio.get_running_loop()

    def status_callback(status_text: str):
        asyncio.run_coroutine_threadsafe(send_status_update(bot, chat_id, status_text, status_message_id, edit_message_id), main_loop)

    def send_video_callback(file_path, hook, start, end):
        caption = get_translation(lang, "video_caption").format(hook=hook, start=start[:-2], end=end[:-2])
        return asyncio.run_coroutine_threadsafe(
            send_video(
                bot,
                chat_id,
                file_path,
                caption,
                edit_message_id,
                FORWARD_RESULTS_GROUP_ID,
                generation_id,
            ),
            main_loop,
        )

    try:
        delete_output = DELETE_OUTPUT_AFTER_SENDING
        
        shorts_generated_count, extra_shorts_found = await asyncio.to_thread(
            process_video,
            user_data['url'],
            user_data['config'],
            status_callback,
            send_video_callback,
            delete_output
        )

        if shorts_generated_count > 0:
            from database import get_user
            _, new_balance, _, lang, _ = get_user(chat_id)
            log_event(chat_id, 'generation_success', {
                'url': user_data['url'],
                'generated_count': shorts_generated_count,
                'generation_id': generation_id
            })
            
            final_message = get_translation(lang, "processing_complete").format(new_balance=new_balance)
            if extra_shorts_found > 0:
                final_message += get_translation(lang, "extra_shorts_found").format(extra_shorts_found=extra_shorts_found)

            # Create rating keyboard
            rating_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(str(i), callback_data=f'rate_{i}') for i in range(1, 6)]
            ])

            # Send one message with keyboard
            await send_message_safely(
                bot,
                chat_id=chat_id,
                text=final_message,
                parse_mode="HTML",
                reply_markup=rating_keyboard,
                reply_to_message_id=status_message_id
            )
        else:
            log_event(chat_id, 'generation_error', {
                'url': user_data['url'], 
                'config': user_data['config'], 
                'error': 'No shorts generated',
                'generation_id': generation_id
            })
            await bot.send_message(
                chat_id=chat_id,
                text=get_translation(lang, "no_shorts_generated"),
                parse_mode="HTML",
                reply_to_message_id=edit_message_id
            )


    except Exception as e:
        logger.error(f"Ошибка при обработке видео для чата {chat_id}: {e}", exc_info=True)
        log_event(chat_id, 'generation_error', {
            'url': user_data.get('url'), 
            'config': user_data.get('config'), 
            'error': str(e),
            'generation_id': generation_id
        })
        await bot.send_message(
            chat_id=chat_id,
            text=get_translation(lang, "critical_processing_error").format(e=e),
            reply_to_message_id=edit_message_id
        )

async def post_init_hook(application: Application):
    """Выполняется после инициализации приложения для настройки фоновых задач."""
    # Создаем и сохраняем очередь в bot_data
    processing_queue = asyncio.Queue()
    application.bot_data['processing_queue'] = processing_queue
    application.bot_data['busy_workers'] = 0
    application.bot_data['busy_workers_lock'] = asyncio.Lock()

    # Загружаем невыполненные задачи из базы данных
    pending_tasks = get_pending_tasks()
    for task in pending_tasks:
        await processing_queue.put(task)
    logger.info(f"Загружено {len(pending_tasks)} невыполненных задач из базы данных.")

    # Запускаем воркеры в зависимости от MAX_CONCURRENT_TASKS
    max_concurrent_tasks = MAX_CONCURRENT_TASKS

    for i in range(max_concurrent_tasks):
        asyncio.create_task(processing_worker(processing_queue, application))
        logger.info(f"Запущен воркер #{i + 1}")

    logger.info(f"Очередь обработки и {max_concurrent_tasks} воркер(а/ов) успешно запущены.")

    # Инициализация аналитической базы данных
    init_analytics_db()

def main():
    """Основная функция для запуска бота."""
    token = TELEGRAM_BOT_TOKEN
    if not token:
        logger.critical("Ошибка: Токен бота не найден. Установите переменную окружения TELEGRAM_BOT_TOKEN.")
        return

    # Create persistence object
    persistence = PicklePersistence(filepath="data/conversation_persistence.pkl")

    application = Application.builder().token(token).persistence(persistence).post_init(post_init_hook).build()

    # Register the error handler
    application.add_error_handler(error_handler)

    conv_handler = get_conv_handler()

    broadcast_topup_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(broadcast_topup_package_selection, pattern=r'^topup_package_')],
        states={
            GET_TOPUP_METHOD: [
                CallbackQueryHandler(topup_stars, pattern='^topup_stars$'),
                CallbackQueryHandler(topup_crypto, pattern='^topup_crypto$'),
                CallbackQueryHandler(topup_yookassa, pattern='^topup_yookassa$'),
                CallbackQueryHandler(back_to_package_selection, pattern='^back_to_package_selection$'),
                CallbackQueryHandler(cancel_topup, pattern='^cancel_topup$')
            ],
            GET_YOOKASSA_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_yookassa_email),
            ],
            CRYPTO_PAYMENT: [
                CallbackQueryHandler(check_crypto_payment, pattern='^check_crypto:'),
                CallbackQueryHandler(back_to_package_selection, pattern='^back_to_package_selection$'),
                CallbackQueryHandler(cancel_topup, pattern='^cancel_topup$')
            ],
            YOOKASSA_PAYMENT: [
                CallbackQueryHandler(check_yookassa_payment, pattern='^check_yookassa:'),
                CallbackQueryHandler(back_to_package_selection, pattern='^back_to_package_selection$'),
                CallbackQueryHandler(cancel_topup, pattern='^cancel_topup$')
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        allow_reentry=True
    )

    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("referral", referral_command))
    application.add_handler(CommandHandler("lang", lang_command))
    application.add_handler(conv_handler)
    application.add_handler(broadcast_topup_handler)
    application.add_handler(CommandHandler("addgenerations", add_generations_command))
    application.add_handler(CommandHandler("setbalance", set_user_balance_command))
    application.add_handler(CommandHandler("rm_user", remove_user_command))
    application.add_handler(CommandHandler("start_discount", start_discount))
    application.add_handler(CommandHandler("end_discount", end_discount))
    application.add_handler(CommandHandler("export_users", export_users_command))
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    application.add_handler(CallbackQueryHandler(set_language, pattern='^set_lang_'))
    # application.add_handler(CallbackQueryHandler(handle_dislike_button, pattern='^dislike$'))
    application.add_handler(CallbackQueryHandler(handle_moderation_button, pattern='^moderate_'))
    application.add_handler(CallbackQueryHandler(handle_feedback_approval, pattern='^(approve|decline)_feedback:'))

    application.add_handler(CommandHandler("status", status_command))

    logger.info("Бот запущен и готов к работе...")
    application.run_polling()

if __name__ == "__main__":
    main()
