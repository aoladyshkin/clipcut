
import sqlite3

DB_FILE = "data/clipcut.db"

def migrate_database():
    """
    Migrates data from generated_shorts_count to generated_count
    and removes the old generated_shorts_count column.
    """
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()

        # Check if generated_shorts_count column exists
        cursor.execute("PRAGMA table_info(users)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if "generated_shorts_count" in columns:
            print("Column 'generated_shorts_count' found. Migrating data...")
            
            # Copy data from generated_shorts_count to generated_count
            cursor.execute("UPDATE users SET generated_count = generated_shorts_count")
            
            print("Data migrated successfully.")

            # I can't drop column in sqlite, so I will create a new table
            
            # 1. Create a new table without the generated_shorts_count column
            cursor.execute(f"""
                CREATE TABLE users_new (
                    user_id INTEGER PRIMARY KEY,
                    balance INTEGER NOT NULL,
                    generated_count INTEGER NOT NULL DEFAULT 0,
                    referred_by INTEGER,
                    source TEXT,
                    language TEXT DEFAULT 'ru'
                )
            """)

            # 2. Copy data from the old table to the new table
            cursor.execute("""
                INSERT INTO users_new (user_id, balance, generated_count, referred_by, source, language)
                SELECT user_id, balance, generated_count, referred_by, source, language FROM users
            """)

            # 3. Drop the old table
            cursor.execute("DROP TABLE users")

            # 4. Rename the new table to the original name
            cursor.execute("ALTER TABLE users_new RENAME TO users")

            print("Column 'generated_shorts_count' removed successfully.")
        else:
            print("Column 'generated_shorts_count' not found. No migration needed.")

        conn.commit()

if __name__ == "__main__":
    migrate_database()
