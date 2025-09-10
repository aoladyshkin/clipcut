# Gemini Code-Context

## Project Overview

This project is a Telegram bot that automatically creates short, viral-style videos from longer YouTube videos. Users interact with the bot to provide a YouTube link and select various options for the output video, such as layout, subtitles, and background video.

The bot uses AI to analyze the video's transcript, identify interesting segments, and then edits them into a vertical format with subtitles. The core logic is encapsulated in `bot_logic.py`, while the Telegram bot interface and conversation management are handled by `bot.py`.

The core technologies used are Python, with the following key libraries:
- `python-telegram-bot` for the Telegram bot interface.
- `moviepy` for video editing.
- `yt-dlp` for downloading YouTube videos.
- `openai` for content analysis (GPT-4).
- `faster-whisper` and `ctranslate2` for efficient, CPU-friendly, word-level audio transcription.
- `python-dotenv` for managing environment variables.

## Project Workflow

1.  **Interaction**: A user starts a conversation with the Telegram bot by sending the `/start` command.
2.  **Configuration**: The bot guides the user through a series of questions to configure the desired video output, including layout, background video, subtitle style, and more.
3.  **Confirmation**: The bot presents a summary of the selected configuration for the user to confirm or cancel.
4.  **Processing**: Upon confirmation, the bot downloads the YouTube video, transcribes the audio, identifies highlights, and generates the short videos.
5.  **Delivery**: The bot sends the generated videos back to the user in the Telegram chat.

## Building and Running

This project is designed to be deployed using Docker. The following instructions are for running the bot on a server or your local machine with Docker.

### 1. Dependencies

All Python dependencies are listed in `requirements.txt` with pinned versions to ensure compatibility. The Dockerfile also installs system-level dependencies like `ffmpeg` and `imagemagick`.

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

### 3. Deployment with Docker

These steps assume you have Docker installed and have cloned the repository to your server.

**A. Prepare Static Files (if necessary)**

The `keepers` directory contains large video files that are not stored in the git repository. If you need them, you must upload this directory to your server's home directory (e.g., `~/keepers`).

**B. Build the Docker Image**

From the root of the project directory, run the build command:
```bash
docker build -t clipcut-bot .
```

**C. Run the Docker Container**

Run the container in detached mode, passing the environment variables from the `.env` file and mounting the `keepers` directory as a volume:
```bash
docker run -d --name clipcut-bot --env-file .env -v ~/keepers:/app/keepers clipcut-bot
```

Your bot is now running. You can view its logs with `docker logs -f clipcut-bot`.