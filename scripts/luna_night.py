#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每晚 22:30 由露娜在群里道晚安。"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "logs" / "luna-goodnight.json"

SEND_HOUR = 22
SEND_MINUTE = 30

LINES = [
    "十点半了。都去睡，别熬着。",
    "夜深了。我该休息了，你们也别硬撑。",
    "该睡了。人类熬坏自己，明天又要怪世界。",
    "晚安。灯关掉，脑子里那些事明天再想。",
    "时间到了。去睡吧，别让我催第二遍。",
]
SISTER_TAIL = "姐姐也早点休息。"


def log(message: str) -> None:
    print(f"[night] {message}", flush=True)


def load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def due(now: datetime) -> bool:
    return now.hour == SEND_HOUR and now.minute == SEND_MINUTE


def run_once(module, groups: list[str], token_state: dict, force: bool = False) -> list[tuple[str, str]]:
    now = datetime.now()
    if not force and not due(now):
        return []
    today = now.strftime("%Y-%m-%d")
    state = load_state()
    if state.get("date") == today and not force:
        return []

    token = str(token_state.get("token") or "").strip()
    if not token:
        return []

    text = random.choice(LINES) + "\n" + SISTER_TAIL
    sent = []
    for group in groups:
        try:
            module.send_group_message(token, group, text)
            sent.append((group, text))
            log(f"sent group={group[:8]}")
        except Exception as exc:
            log(f"send failed group={group[:8]}: {exc}")

    state["date"] = today
    save_state(state)
    return sent
