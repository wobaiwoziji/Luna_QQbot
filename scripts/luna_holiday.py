#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""露娜的节日祝福：每天 09:00 检查日期，是节日就发一句。"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "logs" / "luna-holiday.json"
SEND_HOUR = 9

HOLIDAYS = {
    "01-01": ("元旦", ["新的一年。愿你们今年少些后悔。"], ),
    "02-14": ("情人节", ["这种日子……人类总需要借口表达。"], ),
    "03-08": ("妇女节", ["今天归你们。别把这天也过成加班。"], ),
    "04-01": ("愚人节", ["今天说的话，我一句都不信。"], ),
    "05-01": ("劳动节", ["休息也是命令。今天别硬撑。"], ),
    "06-01": ("儿童节", ["幼稚一天也无妨。"], ),
    "09-10": ("教师节", ["教人的和被教的，都不容易。"], ),
    "10-01": ("国庆", ["出门的话，别把自己弄丢在人堆里。"], ),
    "11-11": ("双十一", ["买之前先想想，你是需要，还是被说服。"], ),
    "12-24": ("平安夜", ["平安夜。别熬太晚。"], ),
    "12-25": ("圣诞", ["人类的节日真多。不过……节日快乐。"], ),
    "12-31": ("跨年", ["这一年快结束了。剩下的时间，别浪费。"], ),
    "2026-02-16": ("除夕", ["除夕。吃完就早点睡。"], ),
    "2026-02-17": ("春节", ["新年。愿你们今年少些后悔。"], ),
    "2026-03-03": ("元宵", ["灯会人多，注意脚下。"], ),
    "2026-06-19": ("端午", ["粽子别吃太多。"], ),
    "2026-09-25": ("中秋", ["月亮还算圆。记得抬头看看。"], ),
    "2026-10-18": ("重阳", ["记得给家里打个电话。"], ),
    "2027-02-05": ("除夕", ["除夕。热闹归热闹，别累着。"], ),
    "2027-02-06": ("春节", ["新年。愿今年顺一点。"], ),
    "2027-02-20": ("元宵", ["元宵节。灯别看得太晚。"], ),
    "2027-06-09": ("端午", ["端午。记得吃口粽子。"], ),
    "2027-09-15": ("中秋", ["中秋。月亮和人都还在，就算不错。"], ),
    "2027-10-08": ("重阳", ["重阳。给家里打个电话。"], ),
}


def log(message: str) -> None:
    print(f"[holiday] {message}", flush=True)


def load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def today_holiday(now: datetime | None = None) -> tuple[str, list[str]] | None:
    now = now or datetime.now()
    for key in (now.strftime("%Y-%m-%d"), now.strftime("%m-%d")):
        if key in HOLIDAYS:
            return HOLIDAYS[key]
    return None


def run_once(module, groups: list[str], token_state: dict) -> list[tuple[str, str]]:
    now = datetime.now()
    if now.hour != SEND_HOUR:
        return []
    found = today_holiday(now)
    if not found:
        return []

    name, lines = found
    today = now.strftime("%Y-%m-%d")
    state = load_state()
    if state.get("date") == today:
        return []

    token = str(token_state.get("token") or "").strip()
    if not token:
        return []

    text = random.choice(lines)
    sent = []
    for group in groups:
        try:
            module.send_group_message(token, group, text)
            sent.append((group, text))
            log(f"sent {name} group={group[:8]}")
        except Exception as exc:
            log(f"send failed group={group[:8]}: {exc}")

    state["date"] = today
    save_state(state)
    return sent
