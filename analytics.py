import os
import json
import logging
from clickhouse_driver import Client

logger = logging.getLogger(__name__)

def get_clickhouse_client():
    """Создает и возвращает клиент для подключения к ClickHouse."""
    try:
        client = Client(
            host=os.environ.get('CLICKHOUSE_HOST'),
            port=int(os.environ.get('CLICKHOUSE_PORT', 9000)),
            user=os.environ.get('CLICKHOUSE_USER', 'default'),
            password=os.environ.get('CLICKHOUSE_PASSWORD', ''),
            database=os.environ.get('CLICKHOUSE_DB', 'default'),
            secure=os.environ.get('CLICKHOUSE_SECURE', 'false').lower() == 'true',
            verify=False
        )
        # Check connection by executing a simple query
        client.execute('SELECT 1')
        logger.info("Successfully connected to ClickHouse.")
        return client
    except Exception as e:
        logger.error(f"Failed to connect to ClickHouse: {e}")
    return None

def init_analytics_db():
    """Инициализирует аналитическую базу данных и создает таблицу событий, если она не существует."""
    client = get_clickhouse_client()
    if not client:
        logger.error("ClickHouse client is not available. Analytics will be disabled.")
        return

    try:
        client.execute("""
            CREATE TABLE IF NOT EXISTS bot_events (
                event_timestamp DateTime DEFAULT now(),
                user_id UInt64,
                event_type String,
                event_data String
            ) ENGINE = MergeTree()
            ORDER BY (event_timestamp, user_id)
        """)
        logger.info("Analytics table 'bot_events' is ready.")
    except Exception as e:
        logger.error(f"Failed to create 'bot_events' table: {e}")
    finally:
        client.disconnect()

def log_event(user_id: int, event_type: str, data: dict):
    """Логирует событие в ClickHouse."""
    client = get_clickhouse_client()
    if not client:
        return

    try:
        event_data_json = json.dumps(data, ensure_ascii=False)
        client.execute(
            "INSERT INTO bot_events (user_id, event_type, event_data) VALUES",
            [(user_id, event_type, event_data_json)]
        )
    except Exception as e:
        logger.error(f"Failed to log event to ClickHouse: {e}")
    finally:
        if client:
            client.disconnect()

