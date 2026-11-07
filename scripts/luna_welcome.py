#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新人欢迎：QQ 机器人接口拿不到「进群」事件，只能按「某人第一次开口」来认新人。"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

import requests

import luna_chat
import token_usage

ROOT = Path(__file__).resolve().parent.parent
SEEN_PATH = ROOT / "logs" / "luna-members.json"
CHAT_DIR = ROOT / "logs" / "chat"
PROFILE_DIR = ROOT / "logs" / "profiles"
GROUP_GAP = 120
MIN_MEMBERS = 5
MAX_USERS = 800

FALLBACKS = [
    "……新面孔。既然进来了，就安分待着。",
    "又来一个人类。……算了，欢迎。",
    "新人？进来就别客气了，这里本来也没什么规矩。",
]


def _load() -> dict:
    try:
        data = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        SEEN_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as exc:
        log(f"members save failed: {exc}")


def log(text: str) -> None:
    print(f"[welcome] {text}", flush=True)


def _seen_before(group: str) -> dict:
    """从聊天记录和身份卡里补出老成员，免得装上新功能后把整个群都欢迎一遍。"""
    users = {}
    folder = CHAT_DIR / group
    for path in sorted(folder.glob("*.jsonl")) if folder.exists() else []:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
                user = str(item.get("user") or "")
                if user and user not in users:
                    users[user] = int(item.get("ts") or 0)
        except Exception:
            continue
    for path in PROFILE_DIR.glob("*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if group in (item.get("groups") or {}):
            users.setdefault(path.stem, int(item.get("firstSeen") or 0))
    return users


def is_new(group: str, user: str) -> bool:
    """首次见到这个人返回 True，之后一直 False。"""
    if not group or not user:
        return False
    data = _load()
    entry = data.setdefault(group, {})
    users = entry.setdefault("users", {})
    if not entry.get("seeded"):
        users.update(_seen_before(group))
        entry["seeded"] = int(time.time())
    new = user not in users
    users[user] = int(users.get(user) or time.time())
    if len(users) > MAX_USERS:
        newest = sorted(users, key=lambda k: users[k])[-MAX_USERS:]
        entry["users"] = {k: users[k] for k in newest}
    _save(data)
    return new


def seed(group: str) -> int:
    """把已有聊天记录里的老成员先记下，避免上线后把整群人都欢迎一遍。"""
    if not group:
        return 0
    data = _load()
    entry = data.setdefault(group, {})
    users = entry.setdefault("users", {})
    users.update(_seen_before(group))
    entry["seeded"] = int(time.time())
    _save(data)
    return len(users)


def size(group: str) -> int:
    return len((_load().get(group) or {}).get("users") or {})


def too_soon(group: str) -> bool:
    last = float((_load().get(group) or {}).get("last_welcome") or 0)
    return time.time() - last < GROUP_GAP


def mark_welcome(group: str) -> None:
    data = _load()
    data.setdefault(group, {})["last_welcome"] = int(time.time())
    _save(data)


def greet(name: str, group: str = "") -> str:
    key = luna_chat.api_key()
    if not key:
        return random.choice(FALLBACKS)
    who = f"「{name}」" if name else "一个没留名字的人"
    prompt = (
        f"群里刚来了个新人，昵称是{who}。用你的口吻对他说一句欢迎的话。"
        "只要一句，不超过 25 字；冷淡、别扭，但别赶人走，不要说教，不要问问题，不用颜文字，不用引号。"
    )
    try:
        response = requests.post(
            luna_chat.API_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": luna_chat.MODEL,
                "messages": [
                    {"role": "system", "content": luna_chat.persona()},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.9,
                "max_tokens": 60,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        token_usage.from_response("welcome", luna_chat.MODEL, payload)
        text = str(payload["choices"][0]["message"]["content"] or "").strip()
        text = text.strip('"“”「」\n ')
        if not text or "[skip]" in text:
            return random.choice(FALLBACKS)
        return text[:60]
    except Exception as exc:
        log(f"greet failed group={group[:8]} {type(exc).__name__}")
        return random.choice(FALLBACKS)
