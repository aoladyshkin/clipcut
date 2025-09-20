import sqlite3
from typing import Optional, Tuple

DB_FILE = "clipcut.db"

def initialize_database():
    """Инициализирует базу данных и создает таблицу, если она не существует."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 10,
                generated_shorts_count INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()

def get_user(user_id: int) -> Optional[Tuple[int, int, int]]:
    """
    Получает данные пользователя по user_id.
    Если пользователь не найден, создает его с балансом по умолчанию.
    """
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, balance, generated_shorts_count FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
        if user is None:
            # Пользователь не найден, создаем нового
            cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
            conn.commit()
            # Возвращаем данные нового пользователя
            return user_id, 10, 0
        return user

def update_user_balance(user_id: int, shorts_generated: int):
    """
    Обновляет баланс пользователя и количество сгенерированных шортсов.
    """
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        # Уменьшаем баланс и увеличиваем счетчик
        cursor.execute(
            "UPDATE users SET balance = balance - ?, generated_shorts_count = generated_shorts_count + ? WHERE user_id = ?",
            (shorts_generated, shorts_generated, user_id)
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

# Убедимся, что база данных инициализируется при запуске
initialize_database()