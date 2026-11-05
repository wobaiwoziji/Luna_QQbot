#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""群聊长期记忆：把聊天记录索引到 SQLite，问到旧事时再检索。"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

import requests

import chat_summary
import token_usage

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "logs" / "memory.db"
CHAT_DIR = ROOT / "logs" / "chat"
KEEP_DAYS = 120
MAX_RESULTS = 3

TRIGGER = re.compile(r"(还记得|记得|上次|之前|以前|那天|说过|提过|聊过|后来|那个)")
STOP_CHARS = set("你还记得我他她它上次之前以前那个说过提聊了的地得吗呢吧啊呀就是有没在讲什么怎么为什么这那你们")
SPLIT = re.compile(r"[^\u4e00-\u9fa5A-Za-z0-9]+")


def log(message: str) -> None:
    print(f"[memory] {message}", flush=True)


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            ts INTEGER NOT NULL,
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            name TEXT,
            text TEXT NOT NULL,
            UNIQUE(group_id, ts, user_id, text)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_group_ts ON messages(group_id, ts)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS summaries (
            group_id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            source_ts INTEGER NOT NULL,
            updated_ts INTEGER NOT NULL
        )
        """
    )
    return conn


def index_group(group_id: str) -> int:
    folder = CHAT_DIR / group_id
    if not folder.exists():
        return 0
    rows = []
    for path in folder.glob("*.jsonl"):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
                text = str(item.get("text") or "").strip()
                ts = int(item.get("ts") or 0)
                if not text or not ts:
                    continue
                rows.append((ts, group_id, str(item.get("user") or ""), str(item.get("name") or "")[:40], text[:300]))
        except Exception:
            continue
    if not rows:
        return 0
    with _conn() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO messages (ts, group_id, user_id, name, text) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def index_all() -> int:
    if not CHAT_DIR.exists():
        return 0
    total = 0
    for folder in CHAT_DIR.iterdir():
        if folder.is_dir():
            try:
                total += index_group(folder.name)
            except Exception as exc:
                log(f"index failed {folder.name[:8]}: {exc}")
    prune()
    return total


def prune() -> None:
    cutoff = int(time.time()) - KEEP_DAYS * 86400
    try:
        with _conn() as conn:
            conn.execute("DELETE FROM messages WHERE ts < ?", (cutoff,))
    except Exception:
        pass


def keywords(text: str) -> list[str]:
    cleaned = "".join(ch for ch in (text or "") if ch not in STOP_CHARS)
    parts = [part for part in SPLIT.split(cleaned) if len(part) >= 2]
    result = []
    for part in parts:
        if part not in result:
            result.append(part[:8])
    return result[:4]


def recall(group_id: str, text: str, limit: int = MAX_RESULTS) -> str:
    if not text or not TRIGGER.search(text):
        return ""
    words = keywords(text)
    if not words:
        return ""
    cutoff = int(time.time()) - KEEP_DAYS * 86400
    clauses = " OR ".join("text LIKE ?" for _ in words)
    params = [group_id, cutoff] + [f"%{word}%" for word in words]
    try:
        with _conn() as conn:
            rows = conn.execute(
                f"SELECT ts, name, text FROM messages WHERE group_id = ? AND ts >= ? AND ({clauses}) "
                "ORDER BY ts DESC LIMIT 80",
                params,
            ).fetchall()
    except Exception as exc:
        log(f"recall failed: {exc}")
        return ""

    scored = []
    for ts, name, content in rows:
        hits = sum(1 for word in words if word in content)
        if not hits:
            continue
        scored.append((hits, ts, name or "群友", content))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)

    lines = []
    for _, ts, name, content in scored[:limit]:
        day = time.strftime("%m-%d", time.localtime(ts))
        lines.append(f"{day} {name}：{content[:70]}")
    return "\n".join(lines)


def recent_summary(group_id: str) -> str:
    try:
        with _conn() as conn:
            row = conn.execute("SELECT text FROM summaries WHERE group_id = ?", (group_id,)).fetchone()
        return str(row[0]) if row else ""
    except Exception:
        return ""


def update_summary(group_id: str, force: bool = False) -> str:
    key = chat_summary.api_key()
    if not key:
        return ""
    with _conn() as conn:
        row = conn.execute("SELECT source_ts, updated_ts FROM summaries WHERE group_id = ?", (group_id,)).fetchone()
        since = int(row[0]) if row else 0
        if row and not force and time.time() - int(row[1] or 0) < 1800:
            return ""
        rows = conn.execute(
            "SELECT ts, name, text FROM messages WHERE group_id = ? AND ts > ? ORDER BY ts ASC LIMIT 80",
            (group_id, since),
        ).fetchall()
    if len(rows) < 20 and not force:
        return ""
    convo = "\n".join(f"{name or '群友'}：{text[:80]}" for _, name, text in rows[-60:])
    prompt = (
        "把下面这段群聊压成两句中文，保留正在聊的话题、约定和结论，不写人名，不用列表。"
        "只输出这两句话。\n\n" + convo
    )
    try:
        response = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-flash",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 120,
                "thinking": {"type": "disabled"},
            },
            timeout=40,
        )
        response.raise_for_status()
        payload = response.json()
        token_usage.from_response("memory", "deepseek-flash", payload)
        text = str(payload["choices"][0]["message"]["content"] or "").strip()
        text = re.sub(r"\s+", " ", text)[:160]
        if not text:
            return ""
        with _conn() as conn:
            conn.execute(
                "INSERT INTO summaries (group_id, text, source_ts, updated_ts) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(group_id) DO UPDATE SET text=excluded.text, source_ts=excluded.source_ts, updated_ts=excluded.updated_ts",
                (group_id, text, int(rows[-1][0]), int(time.time())),
            )
        return text
    except Exception as exc:
        log(f"summary failed: {exc}")
        return ""


def update_all_summaries() -> None:
    if not CHAT_DIR.exists():
        return
    for folder in CHAT_DIR.iterdir():
        if folder.is_dir():
            try:
                update_summary(folder.name)
            except Exception as exc:
                log(f"summary failed {folder.name[:8]}: {exc}")


def reset() -> None:
    try:
        DB_PATH.unlink(missing_ok=True)
    except Exception:
        pass
