import fastapi
import fastapi.middleware.cors
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import json
import os
import sqlite3
import uuid
from dotenv import load_dotenv
from pathlib import Path

try:
    from supabase import create_client, Client
    has_supabase_lib = True
except ImportError:
    create_client = None
    Client = None
    has_supabase_lib = False

load_dotenv()

app = fastapi.FastAPI()

_frontend_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
_extra = os.environ.get("CORS_EXTRA_ORIGINS", "")
if _extra.strip():
    _frontend_origins.extend(o.strip() for o in _extra.split(",") if o.strip())

app.add_middleware(
    fastapi.middleware.cors.CORSMiddleware,
    allow_origins=_frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Supabase client
supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
supabase_anon_key = os.environ.get("SUPABASE_ANON_KEY")

use_supabase = bool(supabase_url and supabase_key and has_supabase_lib)

DB_PATH = Path(__file__).resolve().parent / "dev.db"
local_db = sqlite3.connect(DB_PATH, check_same_thread=False)
local_db.row_factory = sqlite3.Row

def init_local_db():
    with local_db:
        local_db.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL DEFAULT '',
                avatar TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        local_db.execute("""
            CREATE TABLE IF NOT EXISTS journal_entries (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                date TEXT NOT NULL,
                mood TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(device_id, date)
            )
        """)
        local_db.execute("""
            CREATE TABLE IF NOT EXISTS assessment_results (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                date TEXT NOT NULL,
                score INTEGER NOT NULL,
                severity TEXT NOT NULL,
                answers TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )
        """)
        local_db.execute("""
            CREATE TABLE IF NOT EXISTS liked_quotes (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                quote_text TEXT NOT NULL,
                quote_author TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(device_id, quote_text)
            )
        """)

init_local_db()


def local_row_to_dict(row):
    if row is None:
        return None
    data = dict(row)
    if "answers" in data and data["answers"] is not None:
        try:
            data["answers"] = json.loads(data["answers"])
        except Exception:
            pass
    return data


def get_supabase() -> Client:
    if not use_supabase:
        raise fastapi.HTTPException(status_code=500, detail="Supabase configuration missing or unavailable")
    return create_client(supabase_url, supabase_key)

# Pydantic models
class JournalEntry(BaseModel):
    date: str
    mood: str
    content: str
    device_id: str

class AssessmentResult(BaseModel):
    date: str
    score: int
    severity: str
    answers: list[int]
    device_id: str

class UserProfile(BaseModel):
    name: str
    avatar: Optional[str] = None
    device_id: str

class LikedQuote(BaseModel):
    quote_text: str
    quote_author: str
    device_id: str

# Health check
@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

@app.get("/config")
async def get_config():
    if use_supabase and supabase_url and supabase_anon_key:
        return {"supabase_url": supabase_url, "supabase_anon_key": supabase_anon_key}
    return {"supabase_url": "", "supabase_anon_key": ""}

# Journal endpoints
@app.get("/journal")
async def get_journal_entries(device_id: str):
    if use_supabase:
        supabase = get_supabase()
        response = supabase.table("journal_entries").select("*").eq("device_id", device_id).order("date", desc=True).execute()
        return response.data

    response = local_db.execute(
        "SELECT * FROM journal_entries WHERE device_id = ? ORDER BY date DESC",
        (device_id,)
    )
    return [local_row_to_dict(row) for row in response.fetchall()]

@app.post("/journal")
async def add_journal_entry(entry: JournalEntry):
    now = datetime.now().isoformat()

    if use_supabase:
        supabase = get_supabase()
        existing = supabase.table("journal_entries").select("*").eq("device_id", entry.device_id).eq("date", entry.date).execute()
        if existing.data:
            response = supabase.table("journal_entries").update({
                "mood": entry.mood,
                "content": entry.content,
                "updated_at": now
            }).eq("id", existing.data[0]["id"]).execute()
            return response.data[0] if response.data else existing.data[0]
        new_entry = {
            "device_id": entry.device_id,
            "date": entry.date,
            "mood": entry.mood,
            "content": entry.content,
            "created_at": now,
            "updated_at": now
        }
        response = supabase.table("journal_entries").insert(new_entry).execute()
        return response.data[0] if response.data else new_entry

    existing = local_db.execute(
        "SELECT * FROM journal_entries WHERE device_id = ? AND date = ?",
        (entry.device_id, entry.date)
    ).fetchone()

    if existing:
        local_db.execute(
            "UPDATE journal_entries SET mood = ?, content = ?, updated_at = ? WHERE id = ?",
            (entry.mood, entry.content, now, existing["id"])
        )
        updated = local_db.execute("SELECT * FROM journal_entries WHERE id = ?", (existing["id"],)).fetchone()
        return local_row_to_dict(updated)

    entry_id = str(uuid.uuid4())
    local_db.execute(
        "INSERT INTO journal_entries (id, device_id, date, mood, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (entry_id, entry.device_id, entry.date, entry.mood, entry.content, now, now)
    )
    created = local_db.execute("SELECT * FROM journal_entries WHERE id = ?", (entry_id,)).fetchone()
    return local_row_to_dict(created)

@app.put("/journal/{entry_id}")
async def update_journal_entry(entry_id: str, entry: JournalEntry):
    if use_supabase:
        supabase = get_supabase()
        response = supabase.table("journal_entries").update({
            "date": entry.date,
            "mood": entry.mood,
            "content": entry.content,
            "updated_at": datetime.now().isoformat()
        }).eq("id", entry_id).execute()
        if not response.data:
            raise fastapi.HTTPException(status_code=404, detail="Entry not found")
        return response.data[0]

    local_db.execute(
        "UPDATE journal_entries SET date = ?, mood = ?, content = ?, updated_at = ? WHERE id = ?",
        (entry.date, entry.mood, entry.content, datetime.now().isoformat(), entry_id)
    )
    updated = local_db.execute("SELECT * FROM journal_entries WHERE id = ?", (entry_id,)).fetchone()
    if not updated:
        raise fastapi.HTTPException(status_code=404, detail="Entry not found")
    return local_row_to_dict(updated)

@app.delete("/journal/{entry_id}")
async def delete_journal_entry(entry_id: str):
    if use_supabase:
        supabase = get_supabase()
        supabase.table("journal_entries").delete().eq("id", entry_id).execute()
        return {"status": "deleted"}

    local_db.execute("DELETE FROM journal_entries WHERE id = ?", (entry_id,))
    return {"status": "deleted"}

# Assessment endpoints
@app.get("/assessments")
async def get_assessments(device_id: str):
    if use_supabase:
        supabase = get_supabase()
        response = supabase.table("assessment_results").select("*").eq("device_id", device_id).order("created_at", desc=True).execute()
        return response.data

    response = local_db.execute(
        "SELECT * FROM assessment_results WHERE device_id = ? ORDER BY created_at DESC",
        (device_id,)
    )
    return [local_row_to_dict(row) for row in response.fetchall()]

@app.post("/assessments")
async def add_assessment(result: AssessmentResult):
    now = datetime.now().isoformat()
    if use_supabase:
        supabase = get_supabase()
        new_result = {
            "device_id": result.device_id,
            "date": result.date,
            "score": result.score,
            "severity": result.severity,
            "answers": result.answers,
            "created_at": now
        }
        response = supabase.table("assessment_results").insert(new_result).execute()
        return response.data[0] if response.data else new_result

    assessment_id = str(uuid.uuid4())
    local_db.execute(
        "INSERT INTO assessment_results (id, device_id, date, score, severity, answers, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (assessment_id, result.device_id, result.date, result.score, result.severity, json.dumps(result.answers), now)
    )
    created = local_db.execute("SELECT * FROM assessment_results WHERE id = ?", (assessment_id,)).fetchone()
    return local_row_to_dict(created)

@app.delete("/assessments/{assessment_id}")
async def delete_assessment(assessment_id: str):
    if use_supabase:
        supabase = get_supabase()
        supabase.table("assessment_results").delete().eq("id", assessment_id).execute()
        return {"status": "deleted"}

    local_db.execute("DELETE FROM assessment_results WHERE id = ?", (assessment_id,))
    return {"status": "deleted"}

# Profile endpoints
@app.get("/profile")
async def get_profile(device_id: str):
    if use_supabase:
        supabase = get_supabase()
        response = supabase.table("profiles").select("*").eq("device_id", device_id).execute()
        if response.data:
            return {"name": response.data[0]["name"], "avatar": response.data[0].get("avatar")}
        return {"name": "", "avatar": None}

    existing = local_db.execute("SELECT * FROM profiles WHERE device_id = ?", (device_id,)).fetchone()
    if existing:
        return {"name": existing["name"], "avatar": existing["avatar"]}
    return {"name": "", "avatar": None}

@app.put("/profile")
async def update_profile(profile: UserProfile):
    now = datetime.now().isoformat()
    if use_supabase:
        supabase = get_supabase()
        existing = supabase.table("profiles").select("*").eq("device_id", profile.device_id).execute()
        if existing.data:
            supabase.table("profiles").update({
                "name": profile.name,
                "avatar": profile.avatar,
                "updated_at": now
            }).eq("device_id", profile.device_id).execute()
            return {"name": profile.name, "avatar": profile.avatar}
        new_profile = {
            "device_id": profile.device_id,
            "name": profile.name,
            "avatar": profile.avatar,
            "created_at": now,
            "updated_at": now
        }
        supabase.table("profiles").insert(new_profile).execute()
        return {"name": profile.name, "avatar": profile.avatar}

    existing = local_db.execute("SELECT * FROM profiles WHERE device_id = ?", (profile.device_id,)).fetchone()
    if existing:
        local_db.execute(
            "UPDATE profiles SET name = ?, avatar = ?, updated_at = ? WHERE device_id = ?",
            (profile.name, profile.avatar, now, profile.device_id)
        )
        return {"name": profile.name, "avatar": profile.avatar}
    profile_id = str(uuid.uuid4())
    local_db.execute(
        "INSERT INTO profiles (id, device_id, name, avatar, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (profile_id, profile.device_id, profile.name, profile.avatar, now, now)
    )
    return {"name": profile.name, "avatar": profile.avatar}

# Liked quotes endpoints
@app.get("/liked-quotes")
async def get_liked_quotes(device_id: str):
    if use_supabase:
        supabase = get_supabase()
        response = supabase.table("liked_quotes").select("*").eq("device_id", device_id).execute()
        return response.data

    response = local_db.execute("SELECT * FROM liked_quotes WHERE device_id = ?", (device_id,))
    return [local_row_to_dict(row) for row in response.fetchall()]

@app.post("/liked-quotes")
async def toggle_liked_quote(quote: LikedQuote):
    if use_supabase:
        supabase = get_supabase()
        existing = supabase.table("liked_quotes").select("*").eq("device_id", quote.device_id).eq("quote_text", quote.quote_text).execute()
        if existing.data:
            supabase.table("liked_quotes").delete().eq("id", existing.data[0]["id"]).execute()
            return {"action": "unliked", "quote_text": quote.quote_text}
        new_like = {
            "device_id": quote.device_id,
            "quote_text": quote.quote_text,
            "quote_author": quote.quote_author,
            "created_at": datetime.now().isoformat()
        }
        supabase.table("liked_quotes").insert(new_like).execute()
        return {"action": "liked", "quote_text": quote.quote_text}

    existing = local_db.execute(
        "SELECT * FROM liked_quotes WHERE device_id = ? AND quote_text = ?",
        (quote.device_id, quote.quote_text)
    ).fetchone()
    if existing:
        local_db.execute("DELETE FROM liked_quotes WHERE id = ?", (existing["id"],))
        return {"action": "unliked", "quote_text": quote.quote_text}
    local_db.execute(
        "INSERT INTO liked_quotes (id, device_id, quote_text, quote_author, created_at) VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), quote.device_id, quote.quote_text, quote.quote_author, datetime.now().isoformat())
    )
    return {"action": "liked", "quote_text": quote.quote_text}

# Quotes data
@app.get("/quotes")
async def get_quotes():
    return [
        {"text": "You are stronger than you know, braver than you believe, and more loved than you can imagine.", "author": "A.A. Milne", "category": "strength"},
        {"text": "Take a deep breath. It's just a bad day, not a bad life.", "author": "Unknown", "category": "comfort"},
        {"text": "Healing is not linear. Some days will be harder than others, and that's okay.", "author": "Unknown", "category": "healing"},
        {"text": "Be gentle with yourself. You're doing the best you can.", "author": "Unknown", "category": "self-love"},
        {"text": "Your feelings are valid. It's okay to not be okay sometimes.", "author": "Unknown", "category": "comfort"},
        {"text": "Every storm runs out of rain. This too shall pass.", "author": "Maya Angelou", "category": "hope"},
        {"text": "You don't have to control your thoughts. You just have to stop letting them control you.", "author": "Dan Millman", "category": "mindfulness"},
        {"text": "Self-care is not selfish. You cannot serve from an empty vessel.", "author": "Eleanor Brown", "category": "self-love"},
        {"text": "Progress, not perfection, is what we should be asking of ourselves.", "author": "Julia Cameron", "category": "growth"},
        {"text": "The only way out is through. Keep going.", "author": "Robert Frost", "category": "strength"},
        {"text": "You are not your anxiety. You are not your depression. You are you.", "author": "Unknown", "category": "comfort"},
        {"text": "Rest when you need to. The world can wait.", "author": "Unknown", "category": "self-love"},
        {"text": "Small steps every day lead to big changes over time.", "author": "Unknown", "category": "growth"},
        {"text": "Your mental health is a priority. Your happiness is essential. Your self-care is a necessity.", "author": "Unknown", "category": "self-love"},
        {"text": "It's okay to ask for help. Reaching out is a sign of strength, not weakness.", "author": "Unknown", "category": "strength"}
    ]
