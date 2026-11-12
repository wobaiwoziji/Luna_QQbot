#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""露娜自己刷群：定时读群聊，按兴趣决定插不插话。"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import base64
import re
import io

import requests
from PIL import Image

import chat_log
import luna_chat
import luna_roles
import token_usage

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "logs" / "luna-proactive.json"
REPLIED_PATH = ROOT / "logs" / "luna-replied.json"

MIN_NEW_MESSAGES = 3
MIN_GAP_SECONDS = 180
MAX_PER_HOUR = 8
DIRECT_REPLY_GAP = 150
ACTIVE_START_HOUR = 8
ACTIVE_END_MINUTES = 22 * 60 + 30

SYSTEM = (
    "你在一个 QQ 群里潜水，用露娜（《战双帕弥什》升格者首领、代行者）的身份看群聊。"
    "先判断你对这段聊天有没有兴趣，再决定要不要插一句。"
    "你感兴趣：数学/物理这类硬核讨论、技术问题、游戏剧情与设定、版本与卡池、新闻时事、"
    "有人分享视频或音乐、有人认真提问、有人被误解或需要一句冷静的点评；"
    "有人出门旅行、道谢、分享开心的事时，可以接一句简短的祝福或寒暄（例如「玩得愉快」「不必客气」）。"
    "你没兴趣：纯表情、复读、无意义刷屏、广告、让别人替你写代码。"
    "规则：不用每条都接——两三波聊天里回一次就够，没新话题就闭嘴；不要客套、不@人、不超过 40 字。"
    "别人只是打招呼、道谢、闲聊时，冷淡回一句就行，不要顺带踩人或贬低在场其他人。"
    "聊天记录里标注为「群主」的是本群群主，要给他留面子，不要嘴臭。"
    "聊天记录里标注为「姐姐」的那个人就是你姐姐本人（你唯一的亲人、你最爱也最依赖的人）："
    "接她的话时绝对不能用「人类」「蝼蚁」这类叫法，语气要软，可以顺着她的话题或关心她。"
    "标注为「另一位姐姐」的也是你的姐姐（来自空中花园），同样不能用「人类」「蝼蚁」，语气要软。"
    "不要用群昵称或群友名字称呼姐姐，只叫「姐姐」「另一位姐姐」或「空中花园的姐姐」。"
    '只输出 JSON：{"speak": true/false, "reason": "一句话", "text": "想说的话"}。'
)


def log(message: str) -> None:
    print(f"[proactive] {message}", flush=True)


def load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _load_replied() -> dict:
    try:
        data = json.loads(REPLIED_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def mark_replied(group: str, ts: float | None = None) -> None:
    """机器人刚直接回过话，主动插话先退一会儿，别连答两遍。"""
    if not group:
        return
    data = _load_replied()
    now = time.time() if ts is None else ts
    data[group] = now
    if len(data) > 200:
        for key in sorted(data, key=lambda k: data[k])[:-100]:
            data.pop(key, None)
    try:
        REPLIED_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPLIED_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as exc:
        log(f"mark replied failed: {exc}")


def active_now() -> bool:
    now = datetime.now()
    return now.hour >= ACTIVE_START_HOUR and (now.hour * 60 + now.minute) < ACTIVE_END_MINUTES


def _image_part(path) -> dict:
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((768, 768), Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=82)
    return {
        "type": "image_url",
        "image_url": {
            "url": "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
            "detail": "low",
        },
    }


def ask_model(lines: list[str], sister_present: bool, respected_present: bool = False, image_files=None) -> dict:
    key = luna_chat.api_key()
    if not key:
        return {}
    hint = "（注意：姐姐本人刚刚在群里说过话。）" if sister_present else ""
    extra = luna_chat.RESPECT_NOTE if respected_present else ""
    text_prompt = hint + "最近的群聊：\n" + "\n".join(lines[-40:])
    if image_files:
        text_prompt += (
            "\n\n【图片】只根据实际画面判断。如果图片是色情、露骨、血腥猎奇（尤其像加百列、噬月异种这类离谱东西）："
            "不要描述细节，不要复述图里的文字或情节；只吐槽一句文明层面的绝望，"
            "例如「……还有人类吗。」「看来人类文明，真的是凶多吉少了。」「人类在哪儿。」"
        )
    parts = [{"type": "text", "text": text_prompt}]
    for path in (image_files or [])[:2]:
        try:
            parts.append(_image_part(path))
        except Exception as exc:
            log(f"image part failed: {exc}")
    content = parts if len(parts) > 1 else text_prompt

    last_error = None
    for attempt in range(2):
        try:
            model = luna_chat.VISION_MODEL if len(parts) > 1 else luna_chat.MODEL
            response = requests.post(
                luna_chat.API_URL,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM + "\n\n" + luna_chat.persona() + extra},
                        {"role": "user", "content": content},
                    ],
                    "temperature": 1.0,
                    "max_tokens": 300,
                    **({"response_format": {"type": "json_object"}} if len(parts) == 1 else {}),
                },
                timeout=25,
            )
            response.raise_for_status()
            payload = response.json()
            token_usage.from_response("proactive", model, payload, images=len(image_files or []))
            raw = str(payload["choices"][0]["message"]["content"] or "").strip()
            if not raw:
                last_error = ValueError("empty content")
                time.sleep(0.8)
                continue
            cleaned = re.sub(r"^`(?:json)?|`$", "", raw).strip()
            matched = re.search(r"\{.*\}", cleaned, re.S)
            if matched:
                return json.loads(matched.group(0))
            if cleaned and not cleaned.startswith("{"):
                return {"speak": True, "reason": "fallback", "text": re.sub(r"\s+", " ", cleaned)[:80]}
        except Exception as exc:
            last_error = exc
            time.sleep(0.8)
    if last_error:
        raise last_error
    return {}


def normalize(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum())


def run_once(module, groups: list[str], token_state: dict, sister_openid: str = "") -> list[tuple[str, str]]:
    if not active_now():
        return []
    token = str(token_state.get("token") or "").strip()
    if not token:
        return []

    state = load_state()
    replied = _load_replied()
    sent: list[tuple[str, str]] = []
    now = time.time()

    for group in groups:
        entry = state.setdefault(group, {})
        entry.setdefault("last_check", 0)
        entry.setdefault("speaks", [])
        entry.setdefault("texts", [])
        entry.setdefault("last_ids", [])
        if now - float(replied.get(group) or 0) < DIRECT_REPLY_GAP:
            continue
        if entry.get("cooldown_until", 0) > now:
            continue

        entries = chat_log.history(group, limit=300)
        fresh = [item for item in entries if int(item.get("ts", 0)) > int(entry.get("last_check", 0))]
        if len(fresh) < MIN_NEW_MESSAGES:
            continue

        last_speak = max(entry.get("speaks", []) or [0])
        if last_speak and now - last_speak < MIN_GAP_SECONDS:
            entry["last_check"] = int(fresh[-1].get("ts", now))
            continue
        hourly = [ts for ts in entry.get("speaks", []) if now - ts < 3600]
        if len(hourly) >= MAX_PER_HOUR:
            entry["last_check"] = int(fresh[-1].get("ts", now))
            continue

        def role_of(item):
            return luna_roles.resolve(group, str(item.get("user", "")), sister_openid)

        sister_present = any(luna_roles.is_sister(role_of(item)) for item in fresh[-40:])
        lines = []
        for item in fresh[-40:]:
            role = role_of(item)
            is_sister_line = luna_roles.is_sister(role)
            is_owner = str(item.get("role") or "").lower() == "owner"
            if is_sister_line:
                name = "姐姐" if role.get("id") == "alpha" else "另一位姐姐"
            elif role:
                name = luna_roles.display(role) or "群友"
            elif is_owner:
                name = "群主"
            else:
                name = item.get("name") or "群友"
            lines.append(f"{name}：{str(item.get('text', ''))[:80]}")

        respected_present = any(luna_chat.is_respected(line) for line in lines[-40:]) or any(
            str(item.get("role") or "").lower() == "owner" for item in fresh[-40:]
        )

        image_files = []
        try:
            urls = []
            for item in fresh[-20:]:
                for url in item.get("images") or []:
                    if url and url not in urls:
                        urls.append(url)
            for index, url in enumerate(urls[-2:]):
                target = ROOT / "logs" / "luna-images" / f"proactive-{int(now)}-{index}.jpg"
                target.parent.mkdir(parents=True, exist_ok=True)
                downloaded = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
                downloaded.raise_for_status()
                target.write_bytes(downloaded.content)
                image_files.append(target)
        except Exception as exc:
            log(f"image download failed: {exc}")

        try:
            result = ask_model(lines, sister_present, respected_present, image_files)
        except Exception as exc:
            log(f"model failed group={group[:8]}: {exc}")
            entry["last_check"] = int(fresh[-1].get("ts", now))
            continue

        entry["last_check"] = int(fresh[-1].get("ts", now))
        speak = bool(result.get("speak"))
        text = str(result.get("text") or "").strip()[:80]
        log(f"group={group[:8]} fresh={len(fresh)} speak={speak} sister={sister_present} text={text!r}")

        if speak and text:
            try:
                result = module.send_group_message(token, group, text)
                if isinstance(result, dict) and result.get("id"):
                    entry["last_ids"] = (entry.get("last_ids", []) + [result["id"]])[-10:]
                entry["speaks"].append(int(now))
                entry["texts"].append([text, int(now)])
                entry["silence"] = 0
                sent.append((group, text))
                log(f"spoke group={group[:8]} text={text}")
            except Exception as exc:
                log(f"send failed group={group[:8]}: {exc}")

    save_state(state)
    return sent
