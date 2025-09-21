# Аналитические События

Этот документ описывает события, которые бот логирует в базу данных ClickHouse для последующего анализа в Yandex DataLens.

Все события записываются в таблицу `bot_events` и имеют общую структуру:
- `event_timestamp`: Время события
- `user_id`: ID пользователя в Telegram
- `event_type`: Тип события (описаны ниже)
- `event_data`: JSON-объект с дополнительными данными о событии

---

### 1. `new_user`

*   **Когда происходит:** Пользователь впервые запускает бота командой `/start`.
*   **Описание:** Регистрирует появление нового уникального пользователя.
*   **Полезная нагрузка (`event_data`):**
    ```json
    {
      "username": "some_username"
    }
    ```
    *   `username`: Имя пользователя в Telegram (если установлено).

---

### 2. `generation_start`

*   **Когда происходит:** Пользователь подтверждает все настройки и запускает процесс генерации видео.
*   **Описание:** Фиксирует начало выполнения заказа на генерацию.
*   **Полезная нагрузка (`event_data`):**
    ```json
    {
      "url": "https://youtube.com/watch?v=...",
      "config": {
        "shorts_number": 3,
        "layout": "top_bottom",
        "bottom_video": "minecraft",
        "subtitles_type": "word-by-word",
        "subtitle_style": "yellow",
        "force_ai_transcription": false,
        "capitalize_sentences": false
      }
    }
    ```
    *   `url`: Ссылка на исходное YouTube видео.
    *   `config`: JSON-объект со всеми настройками, которые выбрал пользователь.

---

### 3. `generation_success`

*   **Когда происходит:** Процесс обработки видео успешно завершен, и было создано хотя бы одно короткое видео.
*   **Описание:** Фиксирует успешное выполнение заказа.
*   **Полезная нагрузка (`event_data`):**
    ```json
    {
      "url": "https://youtube.com/watch?v=...",
      "config": { ... },
      "generated_count": 3
    }
    ```
    *   `url`, `config`: Аналогично событию `generation_start`.
    *   `generated_count`: Количество фактически созданных шортсов.

---

### 4. `generation_error`

*   **Когда происходит:** Процесс обработки видео завершился с ошибкой на любом этапе, или не было создано ни одного видео.
*   **Описание:** Фиксирует неудачную попытку генерации.
*   **Полезная нагрузка (`event_data`):**
    ```json
    {
      "url": "https://youtube.com/watch?v=...",
      "config": { ... },
      "error": "Текст ошибки"
    }
    ```
    *   `url`, `config`: Аналогично событию `generation_start`.
    *   `error`: Текстовое описание ошибки (например, "No shorts generated" или текст системного исключения).

---

### 5. `payment_success`

*   **Когда происходит:** Пользователь успешно пополняет свой баланс.
*   **Описание:** Фиксирует успешное пополнение.
*   **Полезная нагрузка (`event_data`):**
    ```json
    {
      "provider": "telegram_stars" или "cryptobot",
      "shorts_amount": 10,
      "total_amount": 100,
      "currency": "XTR" или "USDT"
    }
    ```
    *   `provider`: Способ оплаты (`telegram_stars` или `cryptobot`).
    *   `shorts_amount`: Количество купленных "шортсов".
    *   `total_amount`: Сумма платежа в указанной валюте.
    *   `currency`: Валюта платежа (`XTR` для Stars, `USDT` для CryptoBot).
