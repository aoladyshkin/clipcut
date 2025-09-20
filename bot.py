import os
import logging
from dotenv import load_dotenv
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, PreCheckoutQueryHandler, CallbackQueryHandler
import asyncio

from conversation import get_conv_handler
from commands import help_command, balance_command, add_shorts_command, set_user_balance_command
from handlers import precheckout_callback, successful_payment_callback, check_crypto_payment
from processing.bot_logic import main as process_video
from analytics import init_analytics_db, log_event

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()

async def processing_worker(queue: asyncio.Queue, bot: Bot):
    """Воркер, который обрабатывает видео из очереди."""
    while True:
        task_data = await queue.get()
        chat_id = task_data['chat_id']
        user_data = task_data['user_data']
        
        try:
            logger.info(f"Начинаю обработку задачи для чата {chat_id}")
            await run_processing(chat_id, user_data, bot)
        except Exception as e:
            logger.error(f"Ошибка в воркере для чата {chat_id}: {e}", exc_info=True)
            try:
                await bot.send_message(chat_id, f"Произошла критическая ошибка во время обработки вашего видео: {e}")
            except Exception as send_e:
                logger.error(f"Не удалось отправить сообщение об ошибке в чат {chat_id}: {send_e}")
        finally:
            queue.task_done()
            logger.info(f"Завершена обработка задачи для чата {chat_id}. Задач в очереди: {queue.qsize()}")


async def run_processing(chat_id: int, user_data: dict, bot: Bot):
    """Асинхронно запускает обработку видео и отправляет результат."""
    await bot.send_message(chat_id, "⚡ Ваш запрос взят в работу. Начинем скачивание и обработку видео... Это может занять некоторое время.")

    main_loop = asyncio.get_running_loop()

    async def send_status_update_async(status_text: str):
        # Просто отправляем новое сообщение, так как редактирование может быть сложным
        await bot.send_message(
            chat_id=chat_id,
            text=f"{status_text}"
        )

    def send_status_update(status_text: str):
        asyncio.run_coroutine_threadsafe(send_status_update_async(status_text), main_loop)

    async def send_video_async(file_path, hook, start, end):
        caption = f"<b>Hook</b>: {hook}\n\n<b>Таймкоды</b>: {start} – {end}"
        try:
            with open(file_path, 'rb') as video_file:
                await bot.send_video(
                    chat_id=chat_id, 
                    video=video_file, 
                    caption=caption, 
                    parse_mode="HTML", 
                    width=720, 
                    height=1280, 
                    supports_streaming=True,
                    read_timeout=600,
                    write_timeout=600
                )
        except Exception as e:
            logger.error(f"Ошибка при отправке видео {file_path} в чат {chat_id}: {e}")
            await bot.send_message(chat_id, f"Не удалось отправить видео: {file_path}\n\nОшибка: {e}")

    def send_video_callback(file_path, hook, start, end):
        return asyncio.run_coroutine_threadsafe(send_video_async(file_path, hook, start, end), main_loop)

    try:
        delete_output = os.environ.get("DELETE_OUTPUT_AFTER_SENDING", "false").lower() == "true"
        
        shorts_generated_count = await asyncio.to_thread(
            process_video,
            user_data['url'],
            user_data['config'],
            send_status_update,
            send_video_callback,
            delete_output,
            user_balance=user_data.get('balance')
        )

        if shorts_generated_count > 0:
            from database import update_user_balance, get_user
            update_user_balance(chat_id, shorts_generated_count)
            logger.info(f"Баланс пользователя {chat_id} обновлен. Списано {shorts_generated_count} шортсов.")
            _, new_balance, _, _ = get_user(chat_id)
            log_event(chat_id, 'generation_success', {'url': user_data['url'], 'config': user_data['config'], 'generated_count': shorts_generated_count})
            await bot.send_message(
                chat_id=chat_id,
                text=f"✅ <b>Обработка завершена!</b>\n\nВаш новый баланс: {new_balance} шортсов.",
                parse_mode="HTML"
            )
        else:
            log_event(chat_id, 'generation_error', {'url': user_data['url'], 'config': user_data['config'], 'error': 'No shorts generated'})
            await bot.send_message(
                chat_id=chat_id,
                text="<b>Обработка завершена</b>, но не было создано ни одного шортса.\n\nВаш баланс не изменился.",
                parse_mode="HTML"
            )


    except Exception as e:
        logger.error(f"Ошибка при обработке видео для чата {chat_id}: {e}", exc_info=True)
        log_event(chat_id, 'generation_error', {'url': user_data.get('url'), 'config': user_data.get('config'), 'error': str(e)})
        await bot.send_message(
            chat_id=chat_id,
            text=f"Произошла критическая ошибка во время обработки видео: {e}"
        )

async def post_init_hook(application: Application):
    """Выполняется после инициализации приложения для настройки фоновых задач."""
    # Создаем и сохраняем очередь в bot_data
    processing_queue = asyncio.Queue()
    application.bot_data['processing_queue'] = processing_queue

    # Запускаем воркеры в зависимости от MAX_CONCURRENT_TASKS
    try:
        max_concurrent_tasks = int(os.environ.get("MAX_CONCURRENT_TASKS", "1"))
    except ValueError:
        max_concurrent_tasks = 1
        logger.warning("Неверное значение для MAX_CONCURRENT_TASKS, используется значение по умолчанию: 1")

    for i in range(max_concurrent_tasks):
        asyncio.create_task(processing_worker(processing_queue, application.bot))
        logger.info(f"Запущен воркер #{i + 1}")

    logger.info(f"Очередь обработки и {max_concurrent_tasks} воркер(а/ов) успешно запущены.")

    # Инициализация аналитической базы данных
    init_analytics_db()

def main():
    """Основная функция для запуска бота."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.critical("Ошибка: Токен бота не найден. Установите переменную окружения TELEGRAM_BOT_TOKEN.")
        return

    application = Application.builder().token(token).post_init(post_init_hook).build()

    conv_handler = get_conv_handler()

    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("addshorts", add_shorts_command))
    application.add_handler(CommandHandler("setbalance", set_user_balance_command))
    application.add_handler(CallbackQueryHandler(check_crypto_payment, pattern='^check_crypto:'))
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))

    logger.info("Бот запущен и готов к работе...")
    application.run_polling()

if __name__ == "__main__":
    main()