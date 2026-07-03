from google import genai
from google.genai import types
from pydantic import BaseModel
import sqlite3
import uuid
import os
from datetime import datetime

# Initialize genai client
# Will use ANTHROPIC_API_KEY from env, or we can look for GEMINI_API_KEY.
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", "mock_key_if_missing"))

class ChatMessage(BaseModel):
    role: str
    content: str

def get_session_history(db_path: str, session_id: str, limit: int = 7) -> list[ChatMessage]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?", 
            (session_id, limit)
        ).fetchall()
        # Reverse to get chronological order
        return [ChatMessage(role=r["role"], content=r["content"]) for r in reversed(rows)]

def save_message(db_path: str, session_id: str, role: str, content: str):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO messages (message_id, session_id, role, content) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), session_id, role, content)
        )
        conn.commit()

def create_session(db_path: str, title: str = "New Chat") -> str:
    session_id = str(uuid.uuid4())
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO sessions (session_id, title) VALUES (?, ?)", (session_id, title))
        conn.commit()
    return session_id

def list_sessions(db_path: str) -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT session_id, title, created_at FROM sessions ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
