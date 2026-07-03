import sqlite3
import os
from pathlib import Path

db_path = Path(__file__).parent / "chat_history.db"
if db_path.exists():
    os.remove(db_path)

with sqlite3.connect(db_path) as conn:
    conn.execute("""
        CREATE TABLE messages (
            message_id TEXT PRIMARY KEY,
            session_id TEXT,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            title TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
