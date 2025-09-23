import os
import logging
from dotenv import load_dotenv
from telegram import Update, Bot, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, PreCheckoutQueryHandler, CallbackQueryHandler
import asyncio

from conversation import get_conv_handler
from commands import help_command, balance_command, add_shorts_command, set_user_balance_command, start_discount, end_discount, referral_command, remove_user_command, referral_command
from handlers import precheckout_callback, successful_payment_callback
from processing.bot_logic import main as process_video
from states import RATING
from analytics import init_analytics_db, log_event

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()

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
                await application.bot.send_message(chat_id, f"Произошла критическая ошибка во время обработки вашего видео: {e}")
            except Exception as send_e:
                logger.error(f"Не удалось отправить сообщение об ошибке в чат {chat_id}: {send_e}")
        finally:
            queue.task_done()
            logger.info(f"Завершена обработка задачи для чата {chat_id}. Задач в очереди: {queue.qsize()}")


async def run_processing(chat_id: int, user_data: dict, application: Application, status_message_id: int = None):
    """Асинхронно запускает обработку видео и отправляет результат."""
    bot = application.bot
    from database import get_user # Локальный импорт для избежания циклических зависимостей

    # --- Проверка баланса перед началом обработки ---
    _, current_balance, _, _ = get_user(chat_id)
    shorts_to_generate = user_data.get('config', {}).get('shorts_number')

    # Проверяем, если указано конкретное число шортсов
    if isinstance(shorts_to_generate, int) and current_balance < shorts_to_generate:
        logger.warning(f"Отмена задачи для чата {chat_id}: недостаточный баланс. "
                       f"Требуется: {shorts_to_generate}, в наличии: {current_balance}")
        topup_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Пополнить баланс", callback_data='topup_start')]
        ])
        await bot.send_message(
            chat_id,
            f"❌ Не удалось начать обработку видео: на вашем балансе ({current_balance} шортсов) "
            f"недостаточно средств для создания {shorts_to_generate} видео. "
            f"Пожалуйста, пополните баланс.",
            reply_to_message_id=status_message_id,
            reply_markup=topup_keyboard
        )
        return

    # Проверяем, если баланс нулевой (даже для режима 'авто')
    if current_balance <= 0:
        logger.warning(f"Отмена задачи для чата {chat_id}: нулевой баланс.")
        topup_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Пополнить баланс", callback_data='topup_start')]
        ])
        await bot.send_message(
            chat_id,
            f"❌ Не удалось начать обработку видео: на вашем балансе 0 шортсов. "
            f"Пожалуйста, пополните баланс.",
            reply_to_message_id=status_message_id,
            reply_markup=topup_keyboard
        )
        return

    try:
        processing_message = await bot.send_message(
            chat_id, 
            "⚡ Ваш запрос взят в работу. Начинем скачивание и обработку видео... Это может занять некоторое время.",
            reply_to_message_id=status_message_id
        )
        edit_message_id = processing_message.message_id
    except Exception:
        processing_message = await bot.send_message(
            chat_id, 
            "⚡ Ваш запрос взят в работу. Начинем скачивание и обработку видео... Это может занять некоторое время."
        )
        edit_message_id = processing_message.message_id

    main_loop = asyncio.get_running_loop()

    async def send_status_update_async(status_text: str):
        try:
            await bot.send_message(
                text=status_text,
                chat_id=chat_id,
                reply_to_message_id=status_message_id,
            )
        except Exception as e:
            logger.warning(f"Не удалось отредактировать сообщение о статусе: {e}. Отправляю новое.")
            await bot.send_message(chat_id=chat_id, text=status_text, reply_to_message_id=edit_message_id)

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
                    write_timeout=600,
                    reply_to_message_id=edit_message_id
                )
            return True
        except Exception as e:
            logger.error(f"Ошибка при отправке видео {file_path} в чат {chat_id}: {e}")
            return False

    def send_video_callback(file_path, hook, start, end):
        return asyncio.run_coroutine_threadsafe(send_video_async(file_path, hook, start, end), main_loop)

    try:
        delete_output = os.environ.get("DELETE_OUTPUT_AFTER_SENDING", "false").lower() == "true"
        
        shorts_generated_count, extra_shorts_found = await asyncio.to_thread(
            process_video,
            user_data['url'],
            user_data['config'],
            send_status_update,
            send_video_callback,
            delete_output,
            user_balance=current_balance
        )

        if shorts_generated_count > 0:
            from database import update_user_balance, get_user
            update_user_balance(chat_id, shorts_generated_count)
            logger.info(f"Баланс пользователя {chat_id} обновлен. Списано {shorts_generated_count} шортсов.")
            _, new_balance, _, _ = get_user(chat_id)
            log_event(chat_id, 'generation_success', {'url': user_data['url'], 'config': user_data['config'], 'generated_count': shorts_generated_count})
            
            final_message = f"✅ <b>Обработка завершена!</b>\n\nВаш новый баланс: {new_balance} шортсов."
            if extra_shorts_found > 0:
                final_message += f"\n\nℹ️ Найдено еще {extra_shorts_found} подходящих фрагментов, но на них не хватило баланса."

            await bot.send_message(
                chat_id=chat_id,
                text=final_message,
                parse_mode="HTML",
                reply_to_message_id=status_message_id
            )

            # Ask for rating
            rating_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(str(i), callback_data=f'rate_{i}') for i in range(1, 6)]
            ])
            await bot.send_message(
                chat_id=chat_id,
                text="Оцените результат от 1 до 5",
                reply_markup=rating_keyboard,
                reply_to_message_id=status_message_id
            )
        else:
            log_event(chat_id, 'generation_error', {'url': user_data['url'], 'config': user_data['config'], 'error': 'No shorts generated'})
            await bot.send_message(
                chat_id=chat_id,
                text="<b>Обработка завершена</b>, но не было создано ни одного шортса.\n\nВаш баланс не изменился.",
                parse_mode="HTML",
                reply_to_message_id=edit_message_id
            )


    except Exception as e:
        logger.error(f"Ошибка при обработке видео для чата {chat_id}: {e}", exc_info=True)
        log_event(chat_id, 'generation_error', {'url': user_data.get('url'), 'config': user_data.get('config'), 'error': str(e)})
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
    try:
        max_concurrent_tasks = int(os.environ.get("MAX_CONCURRENT_TASKS", "1"))
    except ValueError:
        max_concurrent_tasks = 1
        logger.warning("Неверное значение для MAX_CONCURRENT_TASKS, используется значение по умолчанию: 1")

    for i in range(max_concurrent_tasks):
        asyncio.create_task(processing_worker(processing_queue, application))
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
    application.add_handler(CommandHandler("referral", referral_command))
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("addshorts", add_shorts_command))
    application.add_handler(CommandHandler("setbalance", set_user_balance_command))
    application.add_handler(CommandHandler("rm_user", remove_user_command))
    application.add_handler(CommandHandler("start_discount", start_discount))
    application.add_handler(CommandHandler("end_discount", end_discount))
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))

    logger.info("Бот запущен и готов к работе...")
    application.run_polling()

if __name__ == "__main__":
    main()