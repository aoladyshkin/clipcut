import asyncio
import os
import logging
from telegram.error import Forbidden

logger = logging.getLogger(__name__)

async def simulate_demo_processing(context, chat_id, status_message_id):
    """
    Simulates the video processing for the demo mode.
    Sends status updates and pre-made videos.
    """
    logger.info(f"Starting demo simulation for chat_id: {chat_id}")
    
    try:
        await asyncio.sleep(7) # Initial delay to simulate download

        status_updates = {
            "🔍 Анализируем видео...": 25,
            "🔥 Найдены отрезки для шортсов - 2 шт. Создаем 2 коротких ролика...": 10
        }

        for text, delay in status_updates.items():
            try:
                await context.bot.send_message(chat_id=chat_id, text=text)
                await asyncio.sleep(delay)
            except Forbidden:
                logger.warning(f"Bot is blocked by the user {chat_id}. Stopping demo.")
                return
            except Exception as e:
                logger.warning(f"Could not send message for demo status: {e}")

        demo_shorts_dir = './demo_shorts'
        if not os.path.exists(demo_shorts_dir) or not os.path.isdir(demo_shorts_dir):
            await context.bot.send_message(chat_id=chat_id, text="Ошибка: Директория с демо-видео не найдена.")
            logger.error(f"Demo shorts directory not found at: {demo_shorts_dir}")
            return

        demo_files = sorted([f for f in os.listdir(demo_shorts_dir) if f.endswith(('.mp4', '.mov'))])

        if not demo_files:
            await context.bot.send_message(chat_id=chat_id, text="Ошибка: Демо-видео не найдены в директории.")
            logger.error(f"No videos found in demo shorts directory: {demo_shorts_dir}")
            return

        for filename in demo_files:
            video_path = os.path.join(demo_shorts_dir, filename)
            try:
                with open(video_path, 'rb') as video_file:
                    await context.bot.send_video(chat_id=chat_id, video=video_file)
                await asyncio.sleep(10)
            except Forbidden:
                logger.warning(f"Bot is blocked by the user {chat_id}. Stopping demo video sending.")
                break 
            except Exception as e:
                logger.error(f"Failed to send demo video {filename} to {chat_id}: {e}")
                await context.bot.send_message(chat_id=chat_id, text=f"Не удалось отправить видео {filename}.")

    except Exception as e:
        logger.error(f"An error occurred during demo simulation for chat {chat_id}: {e}", exc_info=True)
        try:
            await context.bot.send_message(chat_id=chat_id, text="Произошла ошибка во время демонстрации.")
        except Exception:
            pass
    finally:
        logger.info(f"Finished demo simulation for chat_id: {chat_id}")
        
        from utils import format_config
        settings_text = format_config(context.user_data['config'], is_demo=True)
        url = context.user_data.get('url', 'N/A')

        await context.bot.send_message(
            chat_id=chat_id, 
            text=f"✅ <b>Демо-обработка завершена!</b>\n\nОбработаное видео: {url}\n\nВыбранные настройки:\n{settings_text}\nЧтобы создать ещё – /start.",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
