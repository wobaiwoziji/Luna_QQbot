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
MIN_SECONDS = 60
SHORT_PATTERN = re.compile(
    r"([0-9]+|[零一二两三四五六七八九十]+|半)\s*(秒钟?|分钟?|分|个?小时|钟头)\s*(?:后|以后|之后)"
)
CN_NUMBER = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _number(value: str):
    if value == "半":
        return 0.5
    if value.isdigit():
        return int(value)
    if "十" in value:
        head, _, tail = value.partition("十")
        tens = CN_NUMBER.get(head, 1) if head else 1
        ones = CN_NUMBER.get(tail, 0) if tail else 0
        return tens * 10 + ones
    return CN_NUMBER.get(value)


def _parse_short(text: str, now: datetime) -> dict | None:
    matched = SHORT_PATTERN.search(text or "")
    if not matched:
        return None
    amount = _number(matched.group(1))
    if amount is None:
        return None
    unit = matched.group(2)
    if "秒" in unit:
        seconds = amount
    elif "小时" in unit or "钟头" in unit:
        seconds = amount * 3600
    else:
        seconds = amount * 60
    if seconds < 1 or seconds > MAX_DAYS * 86400:
        return None
    what = SHORT_PATTERN.sub("", text)
    what = re.sub(r"^\s*(?:提醒我|提醒|记得|到点提醒我)\s*", "", what)
    what = re.sub(r"^\s*(?:叫|让)我\s*", "", what)
    what = re.sub(r"^\s*去\s*", "", what)
    what = what.strip(" ，,。.！!？?~～")
    if not what:
        what = "该做说好的事了"
    return {
        "due": int((now + timedelta(seconds=seconds)).timestamp()),
        "text": what[:60],
        "seconds": seconds,
    }


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
    now = now or datetime.now()
    short = _parse_short(text, now)
    if short:
        return short
    key = chat_summary.api_key()
    if not key:
        return None
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
    seconds = parsed.get("seconds")
    if seconds is not None:
        if seconds < MIN_SECONDS:
            return {"too_short": True}
    elif parsed["due"] - time.time() < MIN_SECONDS - 1:
        return {"too_short": True}
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
    delta = ts - time.time()
    if 0 < delta < 3600:
        return f"{max(1, (int(delta) + 59) // 60)} 分钟后"
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
