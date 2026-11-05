#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""玩家身份卡：本地统计，零 token。每人一张 logs/profiles/<openid>.json。"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "logs" / "profiles"

MAX_RECENT = 5
RECENT_CHARS = 60

TOPICS = {
    "游戏": ("游戏", "卡池", "版本", "抽卡", "副本", "boss", "steam", "上分"),
    "技术": ("代码", "程序", "报错", "服务器", "接口", "python", "java", "算法", "bug", "部署", "电脑", "软件"),
    "数学": ("数学", "物理", "公式", "证明", "积分", "概率", "定理", "方程"),
    "学习": ("考试", "作业", "复习", "上课", "学校", "老师", "论文", "考研", "成绩"),
    "生活": ("吃饭", "睡觉", "上班", "下班", "打工", "天气", "下雨", "回家", "地铁", "工资", "加班"),
    "情绪": ("累", "烦", "难受", "开心", "难过", "焦虑", "失眠", "生气", "无聊"),
    "二次元": ("番", "动画", "漫画", "手办", "cos", "声优", "新番", "轻小说", "同人"),
    "音乐": ("歌", "音乐", "专辑", "live", "演唱会", "曲子"),
    "视频": ("视频", "b站", "bilibili", "直播", "剪辑", "up主"),
    "新闻": ("新闻", "热搜", "政策", "发布", "事件", "时事"),
}

TIER_LABEL = {"sister": "姐姐", "owner": "群主", "member": "群友"}


def _path(user: str) -> Path:
    safe = re.sub(r"[^0-9A-Za-z_-]", "_", str(user or "unknown"))[:64] or "unknown"
    return DIR / f"{safe}.json"


def load(user: str) -> dict:
    try:
        data = json.loads(_path(user).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(user: str, data: dict) -> None:
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        _path(user).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def touch(group: str, user: str, name: str, tier: str, text: str, ts: float | None = None) -> dict:
    """每条消息更新一次卡片（本地统计）。"""
    if not user:
        return {}
    now = int(ts or time.time())
    card = load(user)
    card["name"] = (name or card.get("name") or "").strip()[:32]
    card["tier"] = tier or card.get("tier") or "member"
    card.setdefault("firstSeen", now)
    card["lastSeen"] = now
    card["messages"] = int(card.get("messages") or 0) + 1

    groups = card.setdefault("groups", {})
    groups[group] = int(groups.get(group) or 0) + 1

    plain = re.sub(r"\s+", " ", str(text or "")).strip()
    if plain:
        lowered = plain.lower()
        topics = card.setdefault("topics", {})
        for label, words in TOPICS.items():
            if any(word in lowered for word in words):
                topics[label] = int(topics.get(label) or 0) + 1
        recent = card.setdefault("recent", [])
        recent.append(plain[:RECENT_CHARS])
        card["recent"] = recent[-MAX_RECENT:]

    _save(user, card)
    return card


def summary(user: str, limit: int = 160) -> str:
    """给 prompt 用的短摘要；没卡就返回空串。"""
    card = load(user)
    if not card or not card.get("messages"):
        return ""

    parts = [f"昵称 {card.get('name') or '未知'}"]
    tier = str(card.get("tier") or "member")
    parts.append(TIER_LABEL.get(tier, "群友"))
    parts.append(f"发言 {card.get('messages')} 条")

    topics = card.get("topics") or {}
    hot = [label for label, count in sorted(topics.items(), key=lambda item: item[1], reverse=True) if count >= 2][:3]
    if hot:
        parts.append("常聊 " + "、".join(hot))

    recent = [item for item in card.get("recent") or [] if item][-2:]
    if recent:
        parts.append("最近说过 " + "；".join(f"「{item[:30]}」" for item in recent))

    text = "｜".join(parts)
    return text[:limit]
