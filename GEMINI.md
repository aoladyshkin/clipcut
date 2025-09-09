# Gemini Code-Context

## Project Overview

This project is a Telegram bot that automatically creates short, viral-style videos from longer YouTube videos. Users interact with the bot to provide a YouTube link and select various options for the output video, such as layout, subtitles, and background video.

The bot uses AI to analyze the video's transcript, identify interesting segments, and then edits them into a vertical format with subtitles. The core logic is encapsulated in `bot_logic.py`, while the Telegram bot interface and conversation management are handled by `bot.py`.

The core technologies used are Python, with the following key libraries:
- `python-telegram-bot` for the Telegram bot interface.
- `moviepy` for video editing.
- `yt-dlp` for downloading YouTube videos.
- `openai` for audio transcription (Whisper) and content analysis (GPT-4).
- `whisperx` for word-level timestamp alignment.
- `python-dotenv` for managing environment variables.

## Project Workflow

1.  **Interaction**: A user starts a conversation with the Telegram bot by sending the `/start` command.
2.  **Configuration**: The bot guides the user through a series of questions to configure the desired video output, including layout, background video, subtitle style, and more.
3.  **Confirmation**: The bot presents a summary of the selected configuration for the user to confirm or cancel.
4.  **Processing**: Upon confirmation, the bot downloads the YouTube video, transcribes the audio, identifies highlights, and generates the short videos.
5.  **Delivery**: The bot sends the generated videos back to the user in the Telegram chat.

## Building and Running

To get the project running, you'll need to set up the environment and dependencies.

### 1. Dependencies

The project requires the following Python libraries. It's recommended to use a virtual environment. You can install all dependencies using the `requirements.txt` file:

```bash
pip install -r requirements.txt
```

### 2. Configuration

The script requires API keys and other configuration to be set as environment variables. Create a `.env` file in the root of the project with the following content:

```
OPENAI_API_KEY="your_openai_key"
TELEGRAM_BOT_TOKEN="your_telegram_bot_token"
DELETE_OUTPUT_AFTER_SENDING="true"
```

- `OPENAI_API_KEY`: Your API key for OpenAI services.
- `TELEGRAM_BOT_TOKEN`: The token for your Telegram bot.
- `DELETE_OUTPUT_AFTER_SENDING`: Set to `true` to automatically delete the video files from the server after they have been sent to the user.

### 3. Running

You can run the bot with the following command:

```bash
python3 bot.py
```

The bot will start polling for updates from Telegram. You can then interact with it by sending commands in your Telegram client.