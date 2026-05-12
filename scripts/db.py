"""
Database layer for veo_prompts.db — SQLite, no third-party deps.

Tables: scrapes, tweets, prompts, categories, category_suggestions
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "veo_prompts.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS scrapes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scrape_time  TEXT NOT NULL,
    queries      TEXT NOT NULL,
    config_yaml  TEXT NOT NULL,
    total_raw    INTEGER NOT NULL DEFAULT 0,
    total_dedup  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tweets (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    scrape_id        INTEGER NOT NULL,
    tweet_id         TEXT NOT NULL UNIQUE,
    url              TEXT NOT NULL,
    text             TEXT NOT NULL,
    created_at       TEXT,
    favorite_count   INTEGER DEFAULT 0,
    retweet_count    INTEGER DEFAULT 0,
    reply_count      INTEGER DEFAULT 0,
    view_count       INTEGER DEFAULT 0,
    author_name      TEXT,
    author_screen    TEXT,
    followers_count  INTEGER DEFAULT 0,
    FOREIGN KEY (scrape_id) REFERENCES scrapes(id)
);

CREATE TABLE IF NOT EXISTS prompts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    tweet_id         TEXT NOT NULL,
    scrape_id        INTEGER NOT NULL,
    url              TEXT NOT NULL,
    category         TEXT NOT NULL,
    title            TEXT NOT NULL,
    prompt_text      TEXT NOT NULL,
    notes            TEXT,
    author_name      TEXT,
    author_screen    TEXT,
    followers_count  INTEGER DEFAULT 0,
    likes_count      INTEGER DEFAULT 0,
    retweet_count    INTEGER DEFAULT 0,
    reply_count      INTEGER DEFAULT 0,
    view_count       INTEGER DEFAULT 0,
    extracted_at     TEXT NOT NULL,
    image_gcs_url    TEXT,
    image_generated_at TEXT,
    FOREIGN KEY (scrape_id) REFERENCES scrapes(id)
);

CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    examples    TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    is_active   INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS category_suggestions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    suggested_name  TEXT NOT NULL,
    suggested_desc  TEXT,
    reason          TEXT NOT NULL,
    suggested_by    TEXT NOT NULL,
    prompt_text     TEXT NOT NULL,
    status          TEXT DEFAULT 'pending',
    created_at      TEXT NOT NULL,
    reviewed_at     TEXT,
    reviewed_by     TEXT
);
"""

DEFAULT_CATEGORIES = [
    ("video-generation", "Prompt for AI video models (Veo, Sora, Kling) — may include motion, camera movement, duration"),
    ("image-generation", "Prompt for still image models (Midjourney, DALL-E, Flux)"),
    ("cinematic", "Video/image prompt with film-specific terms (lens, f-stop, depth of field, film grain, aspect ratio)"),
    ("character-design", "Prompt focused on consistent character figures for suitable for animation or comics"),
    ("product-photography", "Prompt for realistic product shots, commercial advertising style"),
    ("other", "Prompt that doesn't fit above categories"),
]

_connection: Optional[sqlite3.Connection] = None


def _conn() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _connection = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _connection.row_factory = sqlite3.Row
    return _connection


def init_db():
    """Create all tables and seed default categories."""
    conn = _conn()
    conn.executescript(SCHEMA)
    _migrate_prompts_image_cols(conn)
    now = datetime.now(timezone.utc).isoformat()
    for name, description in DEFAULT_CATEGORIES:
        conn.execute("""
            INSERT OR IGNORE INTO categories (name, description, created_at, updated_at)
            VALUES (?, ?, ?, ?)
        """, (name, description, now, now))
    conn.commit()


def _migrate_prompts_image_cols(conn: sqlite3.Connection):
    """Add image columns to prompts if they don't exist (for existing DBs)."""
    try:
        conn.execute("ALTER TABLE prompts ADD COLUMN image_gcs_url TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        conn.execute("ALTER TABLE prompts ADD COLUMN image_generated_at TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists


def close():
    global _connection
    if _connection:
        _connection.close()
        _connection = None


# ─── Scrapes ──────────────────────────────────────────────────────────────────

def create_scrape(queries: list[str], config_yaml: str, total_raw: int, total_dedup: int) -> int:
    conn = _conn()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute("""
        INSERT INTO scrapes (scrape_time, queries, config_yaml, total_raw, total_dedup)
        VALUES (?, ?, ?, ?, ?)
    """, (now, json.dumps(queries, ensure_ascii=False), config_yaml, total_raw, total_dedup))
    conn.commit()
    return cur.lastrowid


def get_all_scrapes() -> list[dict]:
    rows = _conn().execute("SELECT * FROM scrapes ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


# ─── Tweets ────────────────────────────────────────────────────────────────────

def insert_tweets(scrape_id: int, tweets: list[dict]):
    """Insert tweets, skip duplicates (tweet_id UNIQUE)."""
    conn = _conn()
    for t in tweets:
        conn.execute("""
            INSERT OR IGNORE INTO tweets
              (scrape_id, tweet_id, url, text, created_at,
               favorite_count, retweet_count, reply_count, view_count,
               author_name, author_screen, followers_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            scrape_id,
            t["tweet_id"],
            t["url"],
            t["text"],
            t.get("created_at"),
            t.get("favorite_count", 0),
            t.get("retweet_count", 0),
            t.get("reply_count", 0),
            t.get("view_count", 0),
            t.get("author", {}).get("name", ""),
            t.get("author", {}).get("screen_name", ""),
            t.get("author", {}).get("followers_count", 0),
        ))
    conn.commit()


def get_tweets_for_scrape(scrape_id: int) -> list[dict]:
    rows = _conn().execute(
        "SELECT * FROM tweets WHERE scrape_id = ?", (scrape_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_recent_tweets(limit: int = 500) -> list[dict]:
    """Get most recent tweets across all scrapes for Phase 2 processing."""
    rows = _conn().execute(
        "SELECT * FROM tweets ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ─── Prompts ──────────────────────────────────────────────────────────────────

def insert_prompts(scrape_id: int, prompts: list[dict]):
    now = datetime.now(timezone.utc).isoformat()
    conn = _conn()
    for p in prompts:
        conn.execute("""
            INSERT INTO prompts
              (tweet_id, scrape_id, url, category, title, prompt_text, notes,
               author_name, author_screen, followers_count,
               likes_count, retweet_count, reply_count, view_count, extracted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            p["tweet_id"],
            scrape_id,
            p["url"],
            p["category"],
            p["title"],
            p["prompt_text"],
            p.get("notes", ""),
            p.get("author", {}).get("name", ""),
            p.get("author", {}).get("screen_name", ""),
            p.get("author", {}).get("followers_count", 0),
            p.get("engagement", {}).get("likes", 0),
            p.get("engagement", {}).get("retweets", 0),
            p.get("engagement", {}).get("replies", 0),
            p.get("engagement", {}).get("views", 0),
            now,
        ))
    conn.commit()


def get_prompts_for_scrape(scrape_id: int) -> list[dict]:
    rows = _conn().execute(
        "SELECT * FROM prompts WHERE scrape_id = ?", (scrape_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_prompts(limit: int = 500) -> list[dict]:
    rows = _conn().execute(
        "SELECT * FROM prompts ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_prompts_without_images(scrape_id: int = None, limit: int = None) -> list[dict]:
    """Get prompts that haven't had images generated yet."""
    sql = "SELECT * FROM prompts WHERE image_gcs_url IS NULL"
    params = []
    if scrape_id is not None:
        sql += " AND scrape_id = ?"
        params.append(scrape_id)
    sql += " ORDER BY id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = _conn().execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def update_prompt_image(prompt_id: int, gcs_url: str):
    """Update a prompt with its generated image GCS URL."""
    now = datetime.now(timezone.utc).isoformat()
    _conn().execute(
        "UPDATE prompts SET image_gcs_url = ?, image_generated_at = ? WHERE id = ?",
        (gcs_url, now, prompt_id)
    )
    _conn().commit()


# ─── Categories ───────────────────────────────────────────────────────────────

def get_categories() -> list[dict]:
    rows = _conn().execute(
        "SELECT * FROM categories WHERE is_active = 1 ORDER BY name"
    ).fetchall()
    return [dict(r) for r in rows]


def add_category(name: str, description: str, examples: list[str] = None) -> int:
    now = datetime.now(timezone.utc).isoformat()
    conn = _conn()
    cur = conn.execute("""
        INSERT INTO categories (name, description, examples, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
    """, (name, description, json.dumps(examples or [], ensure_ascii=False), now, now))
    conn.commit()
    return cur.lastrowid


def deactivate_category(category_id: int):
    now = datetime.now(timezone.utc).isoformat()
    _conn().execute(
        "UPDATE categories SET is_active = 0, updated_at = ? WHERE id = ?",
        (now, category_id)
    )
    _conn().commit()


def update_category(category_id: int, name: str = None, description: str = None):
    conn = _conn()
    now = datetime.now(timezone.utc).isoformat()
    if name is not None:
        conn.execute("UPDATE categories SET name = ?, updated_at = ? WHERE id = ?",
                     (name, now, category_id))
    if description is not None:
        conn.execute("UPDATE categories SET description = ?, updated_at = ? WHERE id = ?",
                     (description, now, category_id))
    conn.commit()


# ─── Category Suggestions ──────────────────────────────────────────────────────

def suggest_category(name: str, reason: str, prompt_text: str,
                    suggested_by: str = "MiniMax-M2.7",
                    description: str = None) -> int:
    now = datetime.now(timezone.utc).isoformat()
    conn = _conn()
    cur = conn.execute("""
        INSERT INTO category_suggestions
          (suggested_name, suggested_desc, reason, suggested_by, prompt_text, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (name, description or "", reason, suggested_by, prompt_text, now))
    conn.commit()
    return cur.lastrowid


def get_pending_suggestions() -> list[dict]:
    rows = _conn().execute(
        "SELECT * FROM category_suggestions WHERE status = 'pending' ORDER BY id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def approve_suggestion(suggestion_id: int, reviewed_by: str = "user") -> int:
    conn = _conn()
    row = conn.execute(
        "SELECT * FROM category_suggestions WHERE id = ?", (suggestion_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Suggestion {suggestion_id} not found")

    suggestion = dict(row)
    cat_id = add_category(
        name=suggestion["suggested_name"],
        description=suggestion["suggested_desc"] or "",
        examples=[suggestion["prompt_text"]],
    )

    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        UPDATE category_suggestions
        SET status = 'approved', reviewed_at = ?, reviewed_by = ?
        WHERE id = ?
    """, (now, reviewed_by, suggestion_id))
    conn.commit()
    return cat_id


def reject_suggestion(suggestion_id: int, reviewed_by: str = "user"):
    now = datetime.now(timezone.utc).isoformat()
    _conn().execute("""
        UPDATE category_suggestions
        SET status = 'rejected', reviewed_at = ?, reviewed_by = ?
        WHERE id = ?
    """, (now, reviewed_by, suggestion_id))
    _conn().commit()


# ─── Export helpers ─────────────────────────────────────────────────────────────

def export_scrape_json(scrape_id: int) -> dict:
    scrape = dict(_conn().execute(
        "SELECT * FROM scrapes WHERE id = ?", (scrape_id,)
    ).fetchone())
    tweets = get_tweets_for_scrape(scrape_id)
    prompts = get_prompts_for_scrape(scrape_id)
    return {
        "scrape": scrape,
        "tweets": tweets,
        "prompts": prompts,
    }


def export_latest_prompts_json() -> dict:
    """Export most recent batch of prompts as JSON (for backward compat)."""
    prompts = get_all_prompts(limit=500)
    if not prompts:
        return {"prompts": [], "total_extracted": 0}
    cats = {}
    for p in prompts:
        cats[p["category"]] = cats.get(p["category"], 0) + 1
    return {
        "extract_time": datetime.now(timezone.utc).isoformat(),
        "llm_model": "MiniMax-M2.7",
        "total_input": len(prompts),
        "total_extracted": len(prompts),
        "categories": cats,
        "prompts": prompts,
    }
