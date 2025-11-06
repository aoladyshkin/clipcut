import sqlite3
from typing import Optional, Tuple
from config import START_BALANCE

DB_FILE = "data/clipcut.db"

def initialize_database():
    """Инициализирует базу данных и создает таблицу, если она не существует."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT {START_BALANCE},
                generated_count INTEGER NOT NULL DEFAULT 0,
                referred_by INTEGER,
                source TEXT,
                language TEXT DEFAULT 'ru'
            )
        """)
        # Check if the generated_count column exists, and add it if it doesn't
        try:
            cursor.execute("SELECT generated_count FROM users LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE users ADD COLUMN generated_count INTEGER NOT NULL DEFAULT 0")
            conn.commit()
            print("Database schema updated: added 'generated_count' column to 'users' table.")

        # Check if the referred_by column exists, and add it if it doesn't
        try:
            cursor.execute("SELECT referred_by FROM users LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
            conn.commit()
            print("Database schema updated: added 'referred_by' column to 'users' table.")
        # Check if the source column exists, and add it if it doesn't
        try:
            cursor.execute("SELECT source FROM users LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE users ADD COLUMN source TEXT")
            conn.commit()
            print("Database schema updated: added 'source' column to 'users' table.")
        # Check if the language column exists, and add it if it doesn't
        try:
            cursor.execute("SELECT language FROM users LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'ru'")
            conn.commit()
            print("Database schema updated: added 'language' column to 'users' table.")
        
        # Check if the first_payment_made column exists, and add it if it doesn't
        try:
            cursor.execute("SELECT has_referral_discount FROM users LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE users ADD COLUMN has_referral_discount BOOLEAN NOT NULL DEFAULT 0")
            conn.commit()
            print("Database schema updated: added 'has_referral_discount' column to 'users' table.")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processing_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_data TEXT NOT NULL,
                status_message_id INTEGER
            )
        """)
        # Check if the user_id column exists, and add it if it doesn't
        try:
            cursor.execute("SELECT user_id FROM processing_queue LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE processing_queue ADD COLUMN user_id INTEGER")
            conn.commit()
            print("Database schema updated: added 'user_id' column to 'processing_queue' table.")
        conn.commit()


def add_task_to_queue(user_id: int, chat_id: int, user_data: str, status_message_id: int) -> int:
    """Добавляет задачу в очередь обработки и возвращает ее ID."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO processing_queue (user_id, chat_id, user_data, status_message_id) VALUES (?, ?, ?, ?)",
            (user_id, chat_id, user_data, status_message_id)
        )
        conn.commit()
        return cursor.lastrowid


def get_queue_position(task_id: int) -> int:
    """Возвращает позицию задачи в очереди."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM processing_queue WHERE id <= ?",
            (task_id,)
        )
        position = cursor.fetchone()[0]
        return position

def get_pending_tasks() -> list:
    """Возвращает все невыполненные задачи из очереди."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, user_id, chat_id, user_data, status_message_id FROM processing_queue")
        return cursor.fetchall()

def get_user_tasks_from_queue(user_id: int) -> list:
    """Возвращает все задачи пользователя из очереди."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, user_data FROM processing_queue WHERE user_id = ? ORDER BY id", (user_id,))
        return cursor.fetchall()

def remove_task_from_queue(task_id: int):
    """Удаляет задачу из очереди по ее ID."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM processing_queue WHERE id = ?", (task_id,))
        conn.commit()


def get_user(user_id: int, referrer_id: Optional[int] = None, source: Optional[str] = None) -> Optional[Tuple[int, int, int, str, bool]]:
    """
    Получает данные пользователя по user_id.
    Если пользователь не найден, создает его с балансом по умолчанию.
    Возвращает кортеж (user_id, balance, generated_count, language, is_new).
    """
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, balance, generated_count, language FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
        if user is None:
            # Пользователь не найден, создаем нового
            cursor.execute("INSERT INTO users (user_id, balance, referred_by, source, language) VALUES (?, ?, ?, ?, ?)", (user_id, START_BALANCE, referrer_id, source, 'ru'))
            conn.commit()
            # Возвращаем данные нового пользователя
            return user_id, START_BALANCE, 0, 'ru', True
        return user + (False,)

def set_user_language(user_id: int, language_code: str):
    """
    Устанавливает язык для пользователя.
    """
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET language = ? WHERE user_id = ?",
            (language_code, user_id)
        )
        conn.commit()

def deduct_generation_from_balance(user_id: int):
    """
    Обновляет баланс пользователя и количество сгенерированных видео.
    """
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        # Уменьшаем баланс и увеличиваем счетчик
        cursor.execute(
            "UPDATE users SET balance = balance - 1, generated_count = generated_count + 1 WHERE user_id = ?",
            (user_id,)
        )
        conn.commit()

def add_to_user_balance(user_id: int, amount: int):
    """
    Добавляет указанное количество к балансу пользователя.
    """
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id)
        )
        conn.commit()

def set_user_balance(user_id: int, amount: int):
    """
    Устанавливает баланс пользователя в указанное значение.
    """
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET balance = ? WHERE user_id = ?",
            (amount, user_id)
        )
        conn.commit()

def get_all_user_ids():
    """Возвращает список всех user_id в базе данных."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        return [row[0] for row in cursor.fetchall()]

def delete_user(user_id: int):
    """Удаляет пользователя из базы данных."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.commit()

def get_all_users_data():
    """Возвращает все данные из таблицы users."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, balance, generated_count, referred_by, source, language FROM users")
        return cursor.fetchall()

def set_referral_discount(user_id: int, status: bool):
    """Sets the referral discount status for a user."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET has_referral_discount = ? WHERE user_id = ?",
            (status, user_id)
        )
        conn.commit()

def has_referral_discount(user_id: int) -> bool:
    """Checks if a user has a referral discount."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT has_referral_discount FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        return result[0] == 1 if result else False

def get_user_referrer(user_id: int) -> Optional[int]:
    """Gets the referrer of a user."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT referred_by FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        return result[0] if result and result[0] else None

def clear_database():
    """
    Удаляет все записи из таблицы users.
    """
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users")
        conn.commit()

# Убедимся, что база данных инициализируется при запуске
initialize_database()

if __name__ == "__main__":
    clear_database()
    # Пример использования функций