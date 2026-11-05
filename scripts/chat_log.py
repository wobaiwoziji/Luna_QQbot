#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Store group chat messages so the bot can summarize the day."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHAT_DIR = ROOT / "logs" / "chat"
MAX_LINES = 1500
KEEP_DAYS = 7
MENTION = re.compile(r"<@!?[^>]+>")


def _group_dir(group_openid: str) -> Path:
    path = CHAT_DIR / group_openid
    path.mkdir(parents=True, exist_ok=True)
    return path


def _day_path(group_openid: str, day: str | None = None) -> Path:
    day = day or datetime.now().strftime("%Y-%m-%d")
    return _group_dir(group_openid) / f"{day}.jsonl"


def clean_text(content: str, attachments: list | None = None) -> str:
    text = MENTION.sub("@某人 ", content or "")
    text = re.sub(r"\s+", " ", text).strip()
    if attachments:
        kinds = {str(item.get("content_type", "")).split("/")[0] for item in attachments}
        if "image" in kinds and not text:
            text = "[图片]"
        elif "voice" in kinds and not text:
            text = "[语音]"
        elif not text:
            text = "[附件]"
    return text


def record(group_openid: str, user_id: str, text: str, name: str = "", role: str = "", images=None) -> None:
    if not text:
        return
    path = _day_path(group_openid)
    entry = {
        "ts": int(time.time()),
        "user": user_id,
        "name": name[:40],
        "role": str(role or "")[:10],
        "images": [str(item) for item in (images or [])][:2],
        "text": text[:300],
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    _trim(path)


def _trim(path: Path) -> None:
    try:
        if path.stat().st_size < 800_000:
            return
        lines = path.read_text(encoding="utf-8").splitlines()[-MAX_LINES:]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def history(group_openid: str, day: str | None = None, limit: int = 400) -> list[dict]:
    path = _day_path(group_openid, day)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            entries.append(json.loads(line))
        except Exception:
            continue
    return entries


def user_history(group_openid: str, user_id: str, limit: int = 40) -> list[dict]:
    entries = history(group_openid, limit=800)
    picked = [item for item in entries if str(item.get("user", "")) == user_id]
    return picked[-limit:]


def user_name(group_openid: str, user_id: str) -> str:
    for item in reversed(history(group_openid, limit=800)):
        if str(item.get("user", "")) == user_id and item.get("name"):
            return str(item["name"])
    return ""


def stats(entries: list[dict]) -> dict:
    users = {}
    for item in entries:
        users[item.get("user", "")] = users.get(item.get("user", ""), 0) + 1
    times = [int(item.get("ts", 0)) for item in entries if item.get("ts")]
    span = ""
    if times:
        start = datetime.fromtimestamp(min(times)).strftime("%H:%M")
        end = datetime.fromtimestamp(max(times)).strftime("%H:%M")
        span = f"{start}–{end}"
    return {"messages": len(entries), "users": len(users), "span": span, "rank": users}


def labels(entries: list[dict]) -> dict[str, str]:
    """Map openid to 用户A/用户B... in order of appearance."""
    mapping = {}
    for item in entries:
        user = item.get("user", "")
        if user and user not in mapping:
            mapping[user] = f"用户{chr(ord('A') + len(mapping) % 26)}"
    return mapping


def cleanup(days: int = KEEP_DAYS) -> None:
    if not CHAT_DIR.exists():
        return
    cutoff = datetime.now() - timedelta(days=days)
    for path in CHAT_DIR.glob("*/*.jsonl"):
        try:
            if datetime.strptime(path.stem, "%Y-%m-%d") < cutoff:
                path.unlink()
        except Exception:
            continue
