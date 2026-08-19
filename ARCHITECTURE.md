# ClipCut — System Architecture

> Техническое описание кодовой базы для разработчиков и ИИ-агентов. Что делает продукт и зачем — см. [PRD.md](PRD.md).

## 1. Обзор системы

ClipCut — монолитное Python-приложение: один процесс — Telegram-бот (`python-telegram-bot`, long polling), внутри которого крутится собственный async-воркер пайплайн для тяжёлой обработки видео (скачивание, транскрипция, ИИ-анализ, монтаж, рендер). Персистентность — SQLite (пользователи/очередь) + ClickHouse (аналитика, опционально). Инфраструктура — один Docker-контейнер, деплой через `docker-compose`.

Нет отдельного бэкенда/API/фронтенда — вся поверхность взаимодействия с пользователем это Telegram Bot API.

## 2. Технологический стек

| Слой | Технология |
|---|---|
| Bot framework | `python-telegram-bot` (async, `ConversationHandler` + `JobQueue`) |
| Скачивание видео | `yt-dlp` (+ `ffmpeg` как external downloader для сегментной загрузки) |
| Транскрипция | YouTube subtitles (через yt-dlp) как основной источник; `faster-whisper` (CPU, int8) как fallback / единственный источник для Twitch и внешних ссылок |
| ИИ-анализ хайлайтов | OpenAI Responses API (`gpt-5-nano`) + временный `vector_store` (file_search) на каждый запрос |
| Видео-монтаж | `moviepy` 1.0.3 (поверх ffmpeg), сырое использование `ffmpeg` CLI для вжигания субтитров |
| Детекция лица | `opencv-python-headless` (Haar cascades, CPU) |
| Субтитры | `pysubs2` / собственный генератор ASS |
| БД (основная) | SQLite, файл `data/clipcut.db`, доступ через `sqlite3` напрямую (без ORM) |
| БД (аналитика) | ClickHouse (`clickhouse-driver`), таблица `dev_sf_events` (или `ANALYTICS_TABLE_NAME`) |
| Платежи | Telegram Stars (нативно), `aiocryptopay` (CryptoBot/USDT), `yookassa` SDK (карты/СБП) |
| Персистентность диалогов | `PicklePersistence` (`data/conversation_persistence.pkl`) — сохраняет состояние `ConversationHandler` между рестартами |
| Контейнеризация | Docker (`python:3.9-slim` + `ffmpeg`, `imagemagick`), `docker-compose` |

## 3. Структура репозитория

```
bot.py                  # Точка входа: инициализация Application, регистрация хендлеров, воркер-пул, error handler
conversation.py         # Главный ConversationHandler — граф состояний диалога генерации/оплаты
states.py               # Enum-подобные константы состояний ConversationHandler (range(24))
commands.py             # Команды бота (/start, /menu, /topup, админ-команды, рассылки)
config.py               # Вся конфигурация из .env + константы продукта (лимиты, пути, VIDEO_MAP)
pricing.py               # Тарифная сетка, конвертация валют, DEMO_CONFIG
database.py              # SQLite: пользователи, баланс, очередь задач (raw SQL, без ORM)
analytics.py             # ClickHouse: логирование событий (см. ANALYTICS_EVENTS.md)
localization.py          # get_translation(lang, key) — обёртка над locales/*.json
utils.py                 # Общие хелперы: форматирование конфига, seconds<->hh:mm:ss, платформа по URL

handlers/                # Обработчики шагов ConversationHandler (async функции telegram-update -> next state)
  generation.py           # Весь флоу настройки генерации: URL -> кол-во -> layout -> brainrot -> face tracking -> субтитры -> confirm
  payment.py              # Флоу пополнения баланса, все 3 платёжных провайдера
  feedback.py             # Рейтинг, текстовый фидбек, дизлайк/модерация
  demo.py                 # Демо-режим (без реальной обработки, симуляция)
  common.py               # cancel_conversation и прочие общие хендлеры

processing/               # Вся "тяжёлая" бизнес-логика вне Telegram-слоя (чистый Python, можно гонять из CLI)
  bot_logic.py             # Оркестратор всего пайплайна генерации: main() -> точка входа из bot.py
  download.py              # yt-dlp обёртки: доступность видео, длительность, heatmap, скачивание аудио/сегментов
  transcription.py         # Получение транскрипта: YouTube subtitles ИЛИ faster-whisper, нормализация сегментов
  gpt.py                   # Промпт и вызов OpenAI для поиска хайлайтов + fallback на случайные сегменты
  layouts.py                # Сборка видео-канваса под конкретный layout (moviepy композиция)
  face_tracker.py           # OpenCV-детекция и сглаженный трекинг лица для динамической обрезки
  subtitles.py              # Генерация ASS-субтитров (по словам/по фразам) из сегментов транскрипта
  demo.py                   # Симуляция обработки для демо-режима (без реального рендера)

locales/                 # ru.json, en.json — переводы UI-строк
config_examples/         # Картинки-превью для inline-кнопок настройки (layout/brainrot/subs examples)
demo_shorts/              # Готовые mp4 для демо-режима
keepers/                  # Библиотека brainrot-видео (НЕ в git, монтируется volume-ом на сервере)
fonts/                    # Шрифты для вжигания субтитров (Montserrat.ttf)
data/                     # SQLite БД + pickle-персистентность диалогов (volume, не в git)
```

## 4. Модель данных

### 4.1. `data/clipcut.db` (SQLite, без ORM, схема мигрируется вручную в `database.initialize_database()` через `ALTER TABLE ... IF NOT EXISTS`-паттерн)

**`users`**
| Колонка | Тип | Назначение |
|---|---|---|
| `user_id` | INTEGER PK | Telegram user id |
| `balance` | INTEGER | Баланс генераций |
| `generated_count` | INTEGER | Счётчик успешных генераций всего |
| `referred_by` | INTEGER | user_id пригласившего |
| `source` | TEXT | Источник трафика (из `?start=`) |
| `language` | TEXT | `ru`/`en` |
| `has_referral_discount` | BOOLEAN | Разовая скидка за то, что пришёл по рефералке |
| `has_subscribed_for_reward` | BOOLEAN | Уже получил бонус за подписку на каналы |

**`processing_queue`**
| Колонка | Тип | Назначение |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | Task id |
| `user_id`, `chat_id` | INTEGER | Кому принадлежит задача |
| `user_data` | TEXT (JSON) | Сериализованный `context.user_data` (url, config, generation_id, ...) |
| `status_message_id` | INTEGER | Telegram message id для ответа со статусом |

Задачи в этой таблице — источник истины для восстановления очереди после рестарта бота (`get_pending_tasks()` вызывается в `post_init_hook`). Запись удаляется по завершении обработки (успех или ошибка), независимо от результата.

### 4.2. ClickHouse: таблица событий (`dev_sf_events` по умолчанию)
```sql
event_timestamp DateTime DEFAULT now(),
user_id UInt64,
event_type String,
event_data String   -- произвольный JSON
```
Append-only лог продуктовых событий. Полный список типов событий и их payload — см. [ANALYTICS_EVENTS.md](ANALYTICS_EVENTS.md) (это источник истины, синхронизировать при изменениях). Подключение опционально — если ClickHouse недоступен, `log_event`/`init_analytics_db` молча логируют ошибку и не роняют бота.

## 5. Основные потоки выполнения

### 5.1. Диалоговый флоу (Telegram-слой)

`conversation.py` определяет единый `ConversationHandler` с состояниями из `states.py`. Входные точки (`entry_points`): команда `/start`, сообщение с распознанным URL (regex по youtube/twitch/drive), кнопка демо, команды `/topup`, `/broadcast*`, `/feedback`.

Граф состояний генерации (упрощённо):
```
GET_URL → GET_SHORTS_NUMBER → GET_LAYOUT ─┬─(1:1/16:9)→ GET_BRAINROT → GET_FACE_TRACKING* → GET_SUBTITLES_TYPE
                                            └─(9:16)────→ GET_FACE_TRACKING → GET_SUBTITLES_TYPE
GET_SUBTITLES_TYPE ─(has subs)→ GET_SUBTITLE_STYLE → [ask_for_banner] → CONFIRM_CONFIG
GET_SUBTITLES_TYPE ─(no subs)──────────────────────→ [ask_for_banner] → CONFIRM_CONFIG
CONFIRM_CONFIG → PROCESSING (после confirm_config кладёт задачу в очередь) → PROCESSING (rate_*) → FEEDBACK → END
```
`*` — face tracking запрашивается только для форматов с вертикальной обрезкой (1:1 и 9:16), не для 16:9.
`ask_for_banner` — шаг виден только админам (`ADMIN_USER_IDS`), для остальных пропускается автоматически.

Вся конфигурация накапливается в `context.user_data['config']` (dict) на протяжении диалога и в конце сериализуется в JSON для очереди.

Персистентность: `PicklePersistence` сохраняет весь `user_data`/`chat_data`/`bot_data` на диск — диалог переживает рестарт бота на середине шага настройки.

### 5.2. Очередь обработки и воркеры (`bot.py`)

- При старте (`post_init_hook`) создаётся `asyncio.Queue`, поднимается `MAX_CONCURRENT_TASKS` воркеров (`processing_worker`), незавершённые задачи из SQLite (`processing_queue`) заливаются в очередь заново.
- `confirm_config` (в `handlers/generation.py`) кладёт задачу одновременно в SQLite (для устойчивости к рестарту) и в in-memory `asyncio.Queue` (для реального исполнения).
- Каждый воркер — бесконечный цикл: берёт задачу из очереди → инкрементит счётчик занятых воркеров (`bot_data['busy_workers']`, под `asyncio.Lock`) → вызывает `run_processing` → в `finally` удаляет задачу из SQLite и декрементит счётчик.
- `run_processing` вызывает тяжёлую синхронную функцию `processing.bot_logic.main` через `asyncio.to_thread` (чтобы не блокировать event loop бота), передавая два callback:
  - `status_callback(text)` — текстовые апдейты пользователю в процессе (через `run_coroutine_threadsafe`, т.к. вызывается из другого потока);
  - `send_video_callback(file_path, hook, start, end, virality_score)` — отправка каждого готового ролика сразу по готовности (стриминг результатов, не ждём весь пакет).
- Позиция в очереди для пользователя считается как `total_queue_length - busy_workers` (сколько задач ещё не начали обрабатываться).

### 5.3. Пайплайн обработки видео (`processing/bot_logic.main`)

Точка входа `main(url, config, status_callback, send_video_callback, deleteOutputAfterSending)`:

1. **Разветвление по платформе.** Twitch → отдельный workflow `main_twitch` (сразу случайная нарезка, без heatmap/GPT, т.к. yt-dlp не даёт субтитры/heatmap для Twitch). YouTube/Drive/general — основной флоу ниже.
2. **Определение длительности видео** — `download.get_video_duration` (yt-dlp, с ffprobe-фолбэком). Критично: без длительности процесс останавливается.
3. **Транскрипция** (`transcribe_audio`) — опциональный шаг:
   - Для YouTube: сначала пытаемся скачать готовые субтитры (`download_captions_from_youtube`, приоритет ru→uk→en, ручные важнее авто).
   - Для general/google_drive: сразу качаем аудиодорожку и гоним через `faster-whisper`.
   - Ошибка на этом шаге не фатальна — просто отключает GPT-стратегию и AI-субтитры (see `get_highlights` пункт 2 ниже упадёт на фолбэк).
4. **Поиск хайлайтов** (`get_highlights`) — три стратегии по убыванию приоритета:
   1. **YouTube Heatmap** («Most Replayed») — скользящее окно по heatmap-данным, ищутся окна с максимальной плотностью реплеев, затем `_refine_heatmap_segment` сужает каждое окно до сабсегмента максимальной плотности.
   2. **GPT** (`processing/gpt.get_highlights_from_gpt`) — транскрипт заливается как временный файл в OpenAI vector store, вызывается `gpt-5-nano` через Responses API с `file_search` tool и очень строгим промптом (только JSON, никакого текста вне массива). После ответа — постобработка: подрезка клипов длиннее 60с, попытка дотянуть конец клипа до конца предложения по субтитрам. Vector store и файл удаляются после запроса (изоляция между запросами).
   3. **Random fallback** (`get_random_highlights` / `generate_random_shorts`) — если GPT не вернул валидный JSON или обработка упала: случайные непересекающиеся отрезки нужной длины, `virality_score` присваивается случайно (5–10).
5. **Сортировка** найденных фрагментов по `virality_score` (убывание) — важные ролики отправляются пользователю первыми.
6. **Скачивание полного видео** (`download.download_full_video`) — весь ролик целиком скачивается локально (ограничение `bestvideo[height<=1080]+bestaudio`, mp4). Это намеренно избегает скачивания по диапазону (`download_ranges`): YouTube ограничивает/дросселирует ranged-запросы к адаптивным форматам высокого качества и тихо откатывается на ~360p, тогда как обычная (полная) загрузка получает честный 1080p. Файл живёт в рабочей временной директории (`out_dir/full_video.mp4`) и гарантированно удаляется по завершении шага 7 (успех или ошибка — `finally`).
7. **Producer-consumer рендеринг** (`orchestrate_clip_creation`):
   - Отдельный `ThreadPoolExecutor(max_workers=1)` последовательно вырезает сегменты из уже скачанного локального файла (`download.cut_video_segment`, чистый `ffmpeg` без сети: `-ss` + `-t` с реэнкодом, чтобы избежать "заморозки" первого кадра и гарантировать точный старт по времени).
   - Второй `ThreadPoolExecutor(max_workers=1)` параллельно (относительно первого) рендерит уже вырезанные сегменты — как только сегмент вырезан, он сразу уходит в очередь на рендер, пока следующий сегмент ещё режется. Это единственная точка реального параллелизма в пайплайне (нарезка и render идут внахлёст, но каждый сам по себе последователен — так исключается перегрузка CPU одновременным рендером/нарезкой нескольких клипов).
   - `_render_clip_from_segment` на каждый сегмент: строит канвас через `layouts._build_video_canvas` (кроп/композиция под layout, опционально трекинг лица через `face_tracker`), генерирует ASS-субтитры (`subtitles.create_ass_subtitles`) если нужно, накладывает баннер (опционально), рендерит через `moviepy` (`write_videofile`), затем **вжигает субтитры отдельным проходом `ffmpeg`** (двухэтапный рендер: сначала видео без звука с реэнкодом, затем ffmpeg сшивает видео+аудио+субтитры в одну команду).
   - После рендера каждого клипа сразу вызывается `send_video_callback` — пользователь получает ролики по одному, не дожидаясь всего пакета.
8. Возвращается количество успешно отправленных роликов — на основании этого числа в `run_processing` списывается ровно одна генерация с баланса (независимо от числа готовых клипов).

### 5.4. Платёжный флоу

Три независимых провайдера, объединённых общим шагом выбора пакета (`select_topup_package` / `broadcast_topup_package_selection`):

- **Telegram Stars** — нативный `send_invoice` с валютой `XTR`, подтверждение через `PreCheckoutQueryHandler` + `MessageHandler(filters.SUCCESSFUL_PAYMENT)` (стандартный Telegram Payments flow, обрабатывается вне `ConversationHandler`, т.к. это отдельные типы апдейтов).
- **CryptoBot** (`aiocryptopay`) — создаётся invoice в USDT, пользователю даётся ссылка на оплату + кнопка «Проверить оплату» (polling по клику, не webhook).
- **ЮKassa** (`yookassa` SDK) — требует email пользователя (для чека, 54-ФЗ), создаётся платёж с redirect-подтверждением, аналогично проверка по кнопке (polling, не webhook).

Во всех трёх случаях при успехе: `add_to_user_balance`, погашение реферальной скидки (`has_referral_discount` → `False`) с начислением бонуса рефереру, `log_event('payment_success', ...)`.

## 6. Внешние интеграции

| Сервис | Назначение | Где в коде | Отказоустойчивость |
|---|---|---|---|
| Telegram Bot API | Весь UI продукта | `bot.py`, `handlers/*` | Критична, единая точка отказа |
| YouTube (через yt-dlp) | Скачивание, субтитры, heatmap, метаданные | `processing/download.py` | Требует `YOUTUBE_COOKIES_FILE` для обхода ограничений; при недоступности видео — понятная ошибка пользователю до старта обработки |
| Twitch (через yt-dlp) | Скачивание VOD | `processing/download.py` | Нет heatmap/субтитров — сразу fallback на случайную нарезку |
| OpenAI API | Выбор хайлайтов (`gpt-5-nano`), Whisper не используется (локальный `faster-whisper` вместо API) | `processing/gpt.py` | Некритична — есть fallback на случайные сегменты |
| ClickHouse | Продуктовая аналитика | `analytics.py` | Некритична — при недоступности бот продолжает работать без логирования |
| YooKassa | Платежи (RUB) | `handlers/payment.py` | Критична только для этого способа оплаты |
| CryptoBot | Платежи (USDT) | `handlers/payment.py` | Критична только для этого способа оплаты |
| Telegram Payments (Stars) | Платежи (XTR) | `bot.py`, `handlers/payment.py` | Критична только для этого способа оплаты |

## 7. Конфигурация и переменные окружения

Вся конфигурация централизована в `config.py`, читается из `.env` (`python-dotenv`). Ключевые переменные:

**Обязательные:**
- `TELEGRAM_BOT_TOKEN` — токен бота.
- `OPENAI_API_KEY` — для GPT-стратегии выбора хайлайтов.

**Опциональные (сервис деградирует, но работает):**
- `ADMIN_GROUP_ID`, `ADMIN_USER_TAG` — куда слать трейсбеки необработанных исключений.
- `MODERATORS_GROUP_ID`, `MODERATORS_USER_TAGS` — модерация дизлайков.
- `FEEDBACK_GROUP_ID` — куда пересылать текстовые отзывы.
- `FORWARD_RESULTS_GROUP_ID` — куда дублировать все сгенерированные видео (для внутреннего контроля качества).
- `ADMIN_USER_IDS` — список ID с доступом к админ-командам и баннерам.
- `REQUIRED_CHANNELS` — каналы для обязательной подписки за бонус.
- `MAX_CONCURRENT_TASKS` — число параллельных воркеров обработки (по умолчанию 1 — обработка видео CPU/GPU-тяжёлая).
- `DELETE_OUTPUT_AFTER_SENDING` — удалять ли временные файлы после отправки (в проде — `true`).
- `YOUTUBE_COOKIES_FILE` — путь к файлу cookies для обхода антибот-защиты YouTube.
- `CRYPTO_BOT_TOKEN`, `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY` — платёжные провайдеры.
- `DATABASE_URL`, `ANALYTICS_DATABASE_URL` — заявлены в конфиге, но фактически используются захардкоженные пути/клиенты (`database.py` использует `data/clipcut.db` напрямую, `analytics.py` — переменные `CLICKHOUSE_*`); при рефакторинге БД-слоя учитывай это расхождение.
- Реферальные/бонусные суммы: `REWARD_FOR_FEEDBACK`, `REWARD_FOR_SUBSCRIPTION`, `START_BALANCE`, `REFERRER_REWARD`.

**Константы продукта (не через .env, см. [PRD.md §4.3](PRD.md#43-ограничения-и-константы-продукта)):** `MAX_SHORTS_PER_VIDEO`, `MIN_SHORT_DURATION`, `MAX_SHORT_DURATION`, `FREESPACE_LIMIT_MB`.

## 8. Деплой и инфраструктура

- **Единственный контейнер**, `Dockerfile`: `python:3.9-slim` + системные зависимости `ffmpeg`, `git`, `imagemagick` (с открытой ImageMagick policy для работы с текстом/изображениями через moviepy).
- **Запуск**: `docker-compose up` — собирает образ, монтирует volume'ы: `./data` (SQLite + pickle-персистентность), `./keepers` (brainrot-видео, не в git — доставляются вручную на сервер), `./fonts`, `./config_examples`, `./demo_shorts`.
- **Один процесс = один бот-инстанс**: нет горизонтального масштабирования на уровне приложения. Масштабирование пропускной способности — только через `MAX_CONCURRENT_TASKS` (параллельные воркеры внутри процесса) и вертикальный скейлинг сервера (CPU для ffmpeg/whisper/opencv).
- **Логи**: json-file драйвер Docker, ротация 30MB.
- **Состояние переживает рестарт**: SQLite (пользователи + очередь) + Pickle (диалоги) — оба в volume `./data`, поэтому редеплой не роняет пользовательские сессии и незавершённые задачи.

## 9. Обработка ошибок

- Глобальный `error_handler` в `bot.py` ловит все необработанные исключения апдейтов, шлёт полный traceback в `ADMIN_GROUP_ID` (если настроен).
- В `processing_worker` ошибки конкретной задачи не роняют воркер — логируются, пользователю уходит сообщение об ошибке, задача удаляется из очереди в `finally`.
- Каждый внешний вызов в `processing/*` обёрнут в try/except с graceful degradation (нет субтитров → нет GPT-стратегии; нет heatmap → GPT; нет GPT → случайные сегменты; нет лиц в кадре → центральная обрезка).
- Ошибки логируются и в ClickHouse как событие (`generation_error`, `video_availability_error`, `send_video_error`) — это основной канал мониторинга качества продукта, не только логи сервера.

## 10. Известные архитектурные особенности и ограничения (для агентов, планирующих рефакторинг)

- **Нет ORM и миграций** — `database.py` использует ручные `ALTER TABLE ... try/except OperationalError` для эволюции схемы. Новые колонки добавляются по этому же паттерну.
- **`processing/bot_logic.py` смешивает оркестрацию и side-effects** — это самый большой и самый central файл пайплайна (632 строки), синхронный (не asyncio), выполняется в отдельном треде через `asyncio.to_thread`. Изменения здесь напрямую влияют на весь путь генерации.
- **`analytics.py` открывает новое соединение с ClickHouse на каждое событие** (`get_clickhouse_client()` внутри `log_event`) — нет пула соединений; при высокой частоте событий это узкое место. Важнее: `log_event()` вызывается **синхронно, прямо в async-хендлерах и в `bot.py`** (не через `asyncio.to_thread`) — если ClickHouse недоступен/подвисает, вызов блокирует **весь event loop бота для всех пользователей** на время таймаута клиента. Таймауты клиента заданы явно (`connect_timeout`/`send_receive_timeout`/`sync_request_timeout=5`), но сама блокирующая природа вызова остаётся архитектурным риском — при рефакторинге стоит завернуть `log_event` в `asyncio.to_thread` или сделать асинхронным/fire-and-forget.
- **GPT-промпт (`processing/gpt.gpt_gpt_prompt`) — часть бизнес-логики, не просто конфиг** — правила длительности клипов, критерии виральности и формат ответа зашиты текстом в промпте; при изменении констант `MIN_SHORT_DURATION`/`MAX_SHORT_DURATION`/`MAX_SHORTS_PER_VIDEO` промпт использует их динамически, но описанная "логика количества клипов по длительности видео" внутри промпта захардкожена отдельно и не связана с кодом программно — надо синхронизировать вручную.
- **`face_tracker.py` — CPU-only OpenCV Haar cascades**, не ML-модель — попроще и предсказуемее по стоимости, но менее точный, чем DNN-детекторы. Если потребуется улучшить точность трекинга — это первая точка замены.
- **Файлы конфигурации диалога (`context.user_data`) сериализуются в JSON и живут в SQLite `processing_queue.user_data`** — при добавлении новых полей в `config` дополнительных миграций не требуется (не типизированная схема), но нужно быть аккуратным с обратной совместимостью для задач, которые были в очереди на момент деплоя новой версии.
- **Полилингвальность реализована как plain dict lookup** (`localization.py`, 16 строк) — при добавлении нового языка: скопировать `locales/en.json`, перевести, добавить обработку в местах выбора языка (`commands.set_language`).
- **Полное видео скачивается на диск целиком перед нарезкой** (`download.download_full_video`, до 1080p) — намеренный trade-off: увеличивает время и место на диске на генерацию (особенно для длинных Twitch VOD/подкастов), но обходит троттлинг YouTube на ranged-загрузку адаптивных форматов, который иначе тихо откатывает качество до ~360p. Файл живёт только на время обработки одной генерации и удаляется сразу после (`finally` в `bot_logic.main`/`handle_random_clips_workflow`), но пиковое потребление диска на одну задачу теперь равно размеру всего исходника, а не только нужных сегментов — учитывай это при увеличении `MAX_CONCURRENT_TASKS` (параллельные задачи одновременно держат на диске несколько полных видео).

## 11. Для ИИ-агентов: как использовать этот документ

- Перед изменением кода в `processing/` — сверься с разделом 5.3 (пайплайн), чтобы понять порядок выполнения шагов и точки graceful degradation.
- Перед изменением диалогового флоу — сверься с разделом 5.1 и убедись, что граф состояний в `conversation.py`/`states.py` остаётся консистентным (каждое новое состояние должно быть в обоих файлах и обработано во всех точках входа/выходов).
- Перед изменением схемы БД — следуй паттерну `try/except OperationalError + ALTER TABLE` в `database.initialize_database()`.
- Если меняешь состав или payload аналитических событий — обнови [ANALYTICS_EVENTS.md](ANALYTICS_EVENTS.md).
- Если меняешь продуктовые правила (тарифы, лимиты, сценарии) — обнови [PRD.md](PRD.md), а не только код.
- Этот документ описывает состояние на момент написания; если реальный код разошёлся с описанием — доверяй коду, но по возможности актуализируй этот файл.
