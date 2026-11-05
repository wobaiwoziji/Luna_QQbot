#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""群聊提醒：把「明天下午三点提醒我交表」解析成定时消息。"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

import chat_summary
import token_usage

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "logs" / "reminders.json"
INTENT = re.compile(r"(提醒我|提醒一下|叫我|记得提醒|到时候提醒|提醒下)")
MAX_DAYS = 365


def log(message: str) -> None:
    print(f"[reminder] {message}", flush=True)


def load() -> list[dict]:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save(items: list[dict]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(items[-200:], ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as exc:
        log(f"save failed: {exc}")


def parse(text: str, now: datetime | None = None) -> dict | None:
    key = chat_summary.api_key()
    if not key:
        return None
    now = now or datetime.now()
    prompt = (
        f"现在是 {now:%Y-%m-%d %H:%M}（周{'一二三四五六日'[now.weekday()]}）。\n"
        f"用户说：「{text[:120]}」\n"
        "把这个提醒解析成 JSON：{\"when\":\"YYYY-MM-DD HH:MM\",\"text\":\"要提醒的事\"}。\n"
        "when 必须晚于现在，且不超过一年；如果推算不出具体时间，输出 {\"when\":\"\",\"text\":\"\"}。\n"
        "只输出 JSON，不要解释。"
    )
    try:
        response = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-flash",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 120,
                "thinking": {"type": "disabled"},
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        token_usage.from_response("reminder", "deepseek-flash", payload)
        raw = str(payload["choices"][0]["message"]["content"] or "")
        matched = re.search(r"\{.*\}", raw, re.S)
        if not matched:
            return None
        data = json.loads(matched.group(0))
        when = str(data.get("when") or "").strip()
        what = str(data.get("text") or "").strip()[:60]
        if not when or not what:
            return None
        due = datetime.strptime(when, "%Y-%m-%d %H:%M")
        if due <= now or due > now + timedelta(days=MAX_DAYS):
            return None
        return {"due": int(due.timestamp()), "text": what}
    except Exception as exc:
        log(f"parse failed: {exc}")
        return None


def maybe_create(group_id: str, user_id: str, name: str, text: str) -> dict | None:
    if not text or not INTENT.search(text):
        return None
    parsed = parse(text)
    if not parsed:
        return None
    items = load()
    items.append(
        {
            "group": group_id,
            "user": user_id,
            "name": str(name or "")[:40],
            "text": parsed["text"],
            "due": parsed["due"],
            "created": int(time.time()),
            "sent": False,
        }
    )
    save(items)
    return parsed


def take_due(now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    items = load()
    due = [item for item in items if not item.get("sent") and int(item.get("due") or 0) <= now]
    if not due:
        return []
    for item in due:
        item["sent"] = True
    save(items)
    return due


def format_due(ts: int) -> str:
    moment = datetime.fromtimestamp(ts)
    today = datetime.now().date()
    if moment.date() == today:
        return moment.strftime("%H:%M")
    if moment.date() == today + timedelta(days=1):
        return "明天 " + moment.strftime("%H:%M")
    return moment.strftime("%m-%d %H:%M")


def pending_for(group_id: str, user_id: str) -> list[dict]:
    return [
        item
        for item in load()
        if not item.get("sent") and item.get("group") == group_id and item.get("user") == user_id
    ]
