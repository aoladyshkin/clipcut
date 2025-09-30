import os
import logging

from telegram import Update, Bot, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, PreCheckoutQueryHandler, CallbackQueryHandler
import asyncio
import telegram.error

from conversation import get_conv_handler
from commands import help_command, balance_command, add_shorts_command, set_user_balance_command, start_discount, end_discount, referral_command, remove_user_command, export_users_command
from handlers import precheckout_callback, successful_payment_callback
from processing.bot_logic import main as process_video
from states import RATING
from analytics import init_analytics_db, log_event
from config import TELEGRAM_BOT_TOKEN, MAX_CONCURRENT_TASKS, FORWARD_RESULTS_GROUP_ID, DELETE_OUTPUT_AFTER_SENDING

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

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
                reply_to_message_id=edit_message_id
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
        await bot.send_message(chat_id, f"Не удалось отправить видео: {file_path}\n\nОшибка: {e}", reply_to_message_id=edit_message_id)
        return False



async def processing_worker(queue: asyncio.Queue, application: Application):
    """Воркер, который обрабатывает видео из очереди."""
    while True:
        task_data = await queue.get()
        chat_id = task_data['chat_id']
        user_data = task_data['user_data']
        status_message_id = task_data.get('status_message_id')
        
        try:
            logger.info(f"Начинаю обработку задачи для чата {chat_id}")
            await run_processing(chat_id, user_data, application, status_message_id)
        except Exception as e:
            logger.error(f"Ошибка в воркере для чата {chat_id}: {e}", exc_info=True)
            try:
                await application.bot.send_message(
                    chat_id,
                    f"Произошла ошибка во время обработки вашего видео:\n\n> {e}",
                    parse_mode="HTML"
                )
            except Exception as send_e:
                logger.error(f"Не удалось отправить сообщение об ошибке в чат {chat_id}: {send_e}")
        finally:
            queue.task_done()
            logger.info(f"Завершена обработка задачи для чата {chat_id}. Задач в очереди: {queue.qsize()}")


async def run_processing(chat_id: int, user_data: dict, application: Application, status_message_id: int = None):
    """Асинхронно запускает обработку видео и отправляет результат."""
    bot = application.bot
    from database import get_user # Локальный импорт для избежания циклических зависимостей

    generation_id = user_data.get('generation_id')
    log_event(chat_id, 'generation_start', {'generation_id': generation_id})

    # --- Проверка баланса перед началом обработки ---
    _, current_balance, _, _ = get_user(chat_id)
    shorts_to_generate = user_data.get('config', {}).get('shorts_number')

    error_message = None
    if current_balance <= 0:
        error_message = "на вашем балансе 0 шортсов"
    elif isinstance(shorts_to_generate, int) and current_balance < shorts_to_generate:
        error_message = f"на вашем балансе ({current_balance} шортсов) недостаточно средств для создания {shorts_to_generate} видео"

    if error_message:
        logger.warning(f"Отмена задачи для чата {chat_id}: {error_message}.")
        topup_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Пополнить баланс", callback_data='topup_start')]
        ])
        await send_message_safely(
            bot,
            chat_id,
            f"❌ Не удалось начать обработку видео: {error_message}. Пожалуйста, пополните баланс.",
            reply_to_message_id=status_message_id,
            reply_markup=topup_keyboard
        )
        return

    processing_message = await send_message_safely(
        bot,
        chat_id,
        "⚡ Ваш запрос взят в работу. Начинем скачивание и обработку видео... Это может занять некоторое время.",
        reply_to_message_id=status_message_id
    )
    edit_message_id = processing_message.message_id if processing_message else None

    main_loop = asyncio.get_running_loop()

    def status_callback(status_text: str):
        asyncio.run_coroutine_threadsafe(send_status_update(bot, chat_id, status_text, status_message_id, edit_message_id), main_loop)

    def send_video_callback(file_path, hook, start, end):
        caption = f"<b>Hook</b>: {hook}\n\n<b>Таймкоды</b>: {start[:-2]} – {end[:-2]}"
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
            delete_output,
            user_balance=current_balance
        )

        if shorts_generated_count > 0:
            from database import update_user_balance, get_user
            update_user_balance(chat_id, shorts_generated_count)
            logger.info(f"Баланс пользователя {chat_id} обновлен. Списано {shorts_generated_count} шортсов.")
            _, new_balance, _, _ = get_user(chat_id)
            log_event(chat_id, 'generation_success', {
                'url': user_data['url'],
                'generated_count': shorts_generated_count,
                'generation_id': generation_id
            })
            
            final_message = f"✅ <b>Обработка завершена!</b>\n\nВаш новый баланс: {new_balance} шортсов.\nПополнить баланс – /topup"
            if extra_shorts_found > 0:
                final_message += f"\n\nℹ️ Найдено еще {extra_shorts_found} подходящих фрагментов, но на них не хватило баланса."

            await send_message_safely(
                bot,
                chat_id=chat_id,
                text=final_message,
                parse_mode="HTML",
                reply_to_message_id=status_message_id
            )

            # Ask for rating
            rating_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(str(i), callback_data=f'rate_{i}') for i in range(1, 6)]
            ])
            await send_message_safely(
                bot,
                chat_id=chat_id,
                text="Оцените результат от 1 до 5",
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
                text="<b>Обработка завершена</b>, но не было создано ни одного шортса.\n\nВаш баланс не изменился.",
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
            text=f"Произошла критическая ошибка во время обработки видео: {e}",
            reply_to_message_id=edit_message_id
        )

async def post_init_hook(application: Application):
    """Выполняется после инициализации приложения для настройки фоновых задач."""
    # Создаем и сохраняем очередь в bot_data
    processing_queue = asyncio.Queue()
    application.bot_data['processing_queue'] = processing_queue

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

    application = Application.builder().token(token).post_init(post_init_hook).build()

    conv_handler = get_conv_handler()

    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("referral", referral_command))
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("addshorts", add_shorts_command))
    application.add_handler(CommandHandler("setbalance", set_user_balance_command))
    application.add_handler(CommandHandler("rm_user", remove_user_command))
    application.add_handler(CommandHandler("start_discount", start_discount))
    application.add_handler(CommandHandler("end_discount", end_discount))
    application.add_handler(CommandHandler("export_users", export_users_command))
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))

    logger.info("Бот запущен и готов к работе...")
    application.run_polling()

if __name__ == "__main__":
    main()