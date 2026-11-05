#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""露娜自己刷 B 站：挑感兴趣的视频，用自己的口吻在群里点评并分享链接。"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import requests

import luna_chat
import token_usage

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "logs" / "luna-bili.json"

POPULAR_URL = "https://api.bilibili.com/x/web-interface/popular"
RANKING_URL = "https://api.bilibili.com/x/web-interface/ranking/v2"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120",
    "Referer": "https://www.bilibili.com/",
}
START_HOUR = 10
END_HOUR = 23
MAX_PER_DAY = 2
MIN_GAP_SECONDS = 90 * 60
MAX_SEEN = 500

JUDGE_SYSTEM = (
    "你是露娜，正在刷 B 站。"
    "从下面的视频里挑一个你真的想看的（最多一个），并用你的口吻写一句点评。"
    "你偏爱：游戏剧情与设定、硬核科普、数学物理、机械与战争、音乐与作画好的短片。"
    "不喜欢：无脑整活、营销号、饭圈、纯擦边。"
    "只输出 JSON：{\"pick\": 序号或 -1, \"comment\": \"不超过 40 字的点评\"}。"
)


def log(message: str) -> None:
    print(f"[bili] {message}", flush=True)


def load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def fetch_candidates(limit: int = 12) -> list[dict]:
    items: list[dict] = []
    for url, params in (
        (POPULAR_URL, {"ps": 20, "pn": 1}),
        (RANKING_URL, {"rid": 0, "type": "all"}),
    ):
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=10)
            payload = response.json() or {}
            for item in (payload.get("data") or {}).get("list") or []:
                items.append(
                    {
                        "bvid": item.get("bvid") or "",
                        "title": str(item.get("title") or "").strip(),
                        "tname": str(item.get("tname") or "").strip(),
                        "owner": str((item.get("owner") or {}).get("name") or "").strip(),
                        "view": int((item.get("stat") or {}).get("view") or 0),
                    }
                )
        except Exception as exc:
            log(f"fetch failed {url}: {exc}")

    unique = {}
    for item in items:
        if item["bvid"] and item["title"]:
            unique.setdefault(item["bvid"], item)
    return list(unique.values())[:limit]


def judge(candidates: list[dict], seen: set[str]) -> tuple[dict | None, str]:
    fresh = [item for item in candidates if item["bvid"] not in seen]
    if not fresh:
        return None, ""
    key = luna_chat.api_key()
    if not key:
        return None, ""

    listing = "\n".join(
        f"{index}. [{item['tname']}] {item['title']}（UP: {item['owner']}）" for index, item in enumerate(fresh)
    )
    try:
        response = requests.post(
            luna_chat.API_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": luna_chat.MODEL,
                "messages": [
                    {"role": "system", "content": JUDGE_SYSTEM + "\n\n" + luna_chat.persona()},
                    {"role": "user", "content": listing},
                ],
                "temperature": 1.0,
                "max_tokens": 200,
                "response_format": {"type": "json_object"},
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        token_usage.from_response("bili", luna_chat.MODEL, payload)
        content = json.loads(payload["choices"][0]["message"]["content"])
        pick = int(payload.get("pick", -1))
        comment = str(payload.get("comment") or "").strip()[:60]
        if 0 <= pick < len(fresh) and comment:
            return fresh[pick], comment
    except Exception as exc:
        log(f"judge failed: {exc}")
    return None, ""


def active_now() -> bool:
    hour = datetime.now().hour
    return START_HOUR <= hour < END_HOUR


def run_once(module, groups: list[str], token_state: dict, dry: bool = False) -> list[tuple[str, str]]:
    token = str(token_state.get("token") or "").strip()
    if not dry and not token:
        return []
    if not active_now():
        return []

    state = load_state()
    seen = set(state.get("seen", [])[-MAX_SEEN:])
    sent: list[tuple[str, str]] = []
    now = time.time()

    candidates = fetch_candidates()
    if not candidates:
        return []
    picked, comment = judge(candidates, seen)
    if not picked:
        log(f"no pick ({len(candidates)} candidates)")
        return []

    text = f"{comment}\n{picked['title']}\nhttps://www.bilibili.com/video/{picked['bvid']}"
    if dry:
        log(f"[dry] pick={picked['title']} comment={comment}")
        return [(groups[0] if groups else "", text)]

    for group in groups:
        entry = state.setdefault(group, {"shares": []})
        today = datetime.now().strftime("%Y-%m-%d")
        shares = [ts for ts in entry.get("shares", []) if datetime.fromtimestamp(ts).strftime("%Y-%m-%d") == today]
        entry["shares"] = shares
        if len(shares) >= MAX_PER_DAY:
            continue
        if shares and now - max(shares) < MIN_GAP_SECONDS:
            continue

        try:
            module.send_group_message(token, group, text)
            entry["shares"].append(int(now))
            sent.append((group, text))
            log(f"shared group={group[:8]} bvid={picked['bvid']}")
        except Exception as exc:
            log(f"send failed group={group[:8]}: {exc}")

    seen.add(picked["bvid"])
    state["seen"] = list(seen)[-MAX_SEEN:]
    save_state(state)
    return sent
