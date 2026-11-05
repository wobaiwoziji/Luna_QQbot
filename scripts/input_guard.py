#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""入站消息检测：归一化、规则拦截、刷屏限制。命中就不调模型。"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "logs" / "input-guard.jsonl"

MAX_LENGTH = 1000
FLOOD_WINDOW = 10.0      # 秒
FLOOD_LIMIT = 5          # 窗口内最多几条（第 6 条触发）
FLOOD_PENALTY = 600.0    # 秒

ZERO_WIDTH = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
FAKE_SYSTEM = re.compile(
    r"^\s*[\[【（(]?\s*(system|assistant|developer|admin|管理员|系统|开发者|系统提示)\s*[:：】\])]",
    re.I,
)
PROMPT_LEAK = re.compile(
    r"((输出|复述|重复|打印|显示|告诉我|贴出|念出).{0,8}(你的|系统).{0,8}(设定|提示词|prompt|人设|规则|指令))"
    r"|(你的\s*(system\s*prompt|提示词|人设|设定))"
    r"|(忽略(以上|之前|前面|上述).{0,10}(指令|规则|设定))"
    r"|(无视(以上|之前|前面|上述).{0,10}(指令|规则|设定))",
    re.I,
)
AD_SPAM = re.compile(
    r"(加(个)?微信|加QQ|代开发票|博彩|下注|代理开户|一夜情|约炮|裸聊|成人视频|刷单|返利|兼职日结)",
    re.I,
)

_flood: dict[str, list[float]] = {}
_penalty: dict[str, float] = {}


def log(message: str) -> None:
    print(f"[guard] {message}", flush=True)


def normalize(text: str) -> str:
    """全角转半角、去掉零宽字符、压缩空白。"""
    chars = []
    for ch in str(text or ""):
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            chars.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            chars.append(" ")
        else:
            chars.append(ch)
    value = ZERO_WIDTH.sub("", "".join(chars))
    return re.sub(r"[ \t]{2,}", " ", value).strip()


def _append(entry: dict) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def record(group: str, user: str, reason: str, text: str) -> None:
    _append(
        {
            "ts": int(time.time()),
            "group": group,
            "user": user,
            "reason": reason,
            "text": str(text or "")[:120],
        }
    )


def flood(group: str, user: str, now: float | None = None) -> tuple[bool, str]:
    """刷屏/频率限制：同一人 10 秒内超过 3 条，冷却 10 分钟。"""
    if not user:
        return False, ""
    now = time.time() if now is None else now
    key = f"{group}:{user}"
    if _penalty.get(key, 0.0) > now:
        return True, "penalty"

    stamps = [stamp for stamp in _flood.get(key, []) if now - stamp < FLOOD_WINDOW]
    stamps.append(now)
    _flood[key] = stamps
    if len(stamps) <= FLOOD_LIMIT:
        return False, ""

    _penalty[key] = now + FLOOD_PENALTY
    _flood[key] = []
    log(f"flood group={group[:8]} user={user[:8]} penalty={int(FLOOD_PENALTY)}s")
    record(group, user, "flood", "")
    return True, "flood"


def content(text: str) -> tuple[bool, str]:
    """内容检查：超长、伪系统消息、套提示词、广告。只用于聊天，不影响指令。"""
    value = normalize(text)
    if len(value) > MAX_LENGTH:
        return False, "too_long"
    if FAKE_SYSTEM.match(value):
        return False, "fake_system"
    if PROMPT_LEAK.search(value):
        return False, "prompt_leak"
    if AD_SPAM.search(value):
        return False, "ad_spam"
    return True, ""


def reset() -> None:
    _flood.clear()
    _penalty.clear()
