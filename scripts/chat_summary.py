#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turn the day's group chat into a summary card image."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chat_log
import token_usage

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG = HERE / "qq-channel.config.json"
OUT_DIR = ROOT / "logs" / "summary-cache"
FONT_REGULAR = Path(r"C:\Windows\Fonts\msyh.ttc")
FONT_BOLD = Path(r"C:\Windows\Fonts\msyhbd.ttc")

API_URL = "https://api.deepseek.com/chat/completions"
MIN_MESSAGES = 5
WIDTH = 1000
PAD = 56
LINE_GAP = 18

BG = (23, 24, 28)
CARD = (31, 32, 38)
TEXT = (245, 245, 247)
MUTED = (154, 154, 162)
ACCENT = (255, 107, 107)

STOPWORDS = {"什么", "怎么", "这个", "那个", "就是", "可以", "没有", "不是", "我们", "你们", "他们", "一个", "现在", "还是", "但是", "因为", "所以", "感觉", "真的", "哈哈"}


def log(message: str) -> None:
    print(f"[summary] {message}", flush=True)


def api_key() -> str:
    try:
        config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
        key = str(config.get("deepseekApiKey") or "").strip()
        if key:
            return key
    except Exception:
        pass
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    return ""


def llm_summary(entries: list[dict], mapping: dict[str, str]) -> dict | None:
    key = api_key()
    if not key:
        return None
    convo = "\n".join(
        f"{mapping.get(item.get('user', ''), '某人')}: {str(item.get('text', ''))[:200]}"
        for item in entries[-300:]
    )
    prompt = (
        "下面是今天一个QQ群的聊天记录，请用简单口语化的中文总结。\n"
        '只输出 JSON，格式：{"headline": "一句话概括今天聊了什么", "points": ["3到6条要点"], "quote": "一句最有意思的原话"}。\n'
        "要求：不要编造没出现的内容；每条要点 15-40 字；不要用 markdown；不要提到 openid；昵称用记录里的 用户A/用户B。\n\n"
        f"聊天记录：\n{convo}"
    )
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是群聊总结助手，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.6,
        "max_tokens": 800,
        "response_format": {"type": "json_object"},
    }
    try:
        response = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=45,
        )
        response.raise_for_status()
        data = response.json()
        token_usage.from_response("summary", "deepseek-chat", data)
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        headline = str(parsed.get("headline") or "").strip()
        points = [str(item).strip() for item in parsed.get("points") or [] if str(item).strip()]
        quote = str(parsed.get("quote") or "").strip()
        if headline and points:
            return {"headline": headline, "points": points[:6], "quote": quote}
    except Exception as exc:
        log(f"LLM 总结失败，改用本地规则: {exc}")
    return None


def local_summary(entries: list[dict], mapping: dict[str, str], stat: dict) -> dict:
    rank = sorted(stat["rank"].items(), key=lambda item: item[1], reverse=True)[:3]
    active = "、".join(f"{mapping.get(user, '某人')}({count})" for user, count in rank if user)
    counter = {}
    for item in entries:
        for token in re.findall(r"[\u4e00-\u9fa5]{2,4}", str(item.get("text", ""))):
            if token in STOPWORDS:
                continue
            counter[token] = counter.get(token, 0) + 1
    hot = "、".join(word for word, count in sorted(counter.items(), key=lambda item: item[1], reverse=True)[:5] if count > 1)
    longest = max((str(item.get("text", "")) for item in entries), key=len, default="")
    points = [f"今天共有 {stat['messages']} 条消息，{stat['users']} 人参与。"]
    if active:
        points.append(f"最活跃：{active}。")
    if hot:
        points.append(f"高频词：{hot}。")
    if longest:
        points.append(f"最长的一条有 {len(longest)} 个字。")
    return {
        "headline": f"今天群里聊了 {stat['messages']} 条消息",
        "points": points,
        "quote": longest[:40],
    }


def wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines = []
    current = ""
    for char in text:
        if char == "\n":
            lines.append(current)
            current = ""
            continue
        trial = current + char
        if draw.textlength(trial, font=font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def build(group_openid: str, day: str | None = None) -> Path:
    entries = chat_log.history(group_openid, day=day, limit=400)
    if len(entries) < MIN_MESSAGES:
        raise ValueError(f"{len(entries)}")

    mapping = chat_log.labels(entries)
    stat = chat_log.stats(entries)
    summary = llm_summary(entries, mapping) or local_summary(entries, mapping, stat)

    date_text = datetime.now().strftime("%Y年%m月%d日")
    title_font = ImageFont.truetype(str(FONT_BOLD), 44)
    meta_font = ImageFont.truetype(str(FONT_REGULAR), 24)
    head_font = ImageFont.truetype(str(FONT_BOLD), 32)
    body_font = ImageFont.truetype(str(FONT_REGULAR), 28)
    quote_font = ImageFont.truetype(str(FONT_REGULAR), 26)
    footer_font = ImageFont.truetype(str(FONT_REGULAR), 22)

    probe = ImageDraw.Draw(Image.new("RGB", (WIDTH, 10)))
    inner = WIDTH - PAD * 2 - 24

    head_lines = wrap(probe, summary["headline"], head_font, inner)
    point_lines = [wrap(probe, point, body_font, inner - 34) for point in summary["points"]]
    quote_text = f"「{summary['quote']}」" if summary.get("quote") else ""
    quote_lines = wrap(probe, quote_text, quote_font, inner - 40)

    height = PAD
    height += 58 + 8 + 34 + 40
    height += len(head_lines) * (head_font.size + 14) + 26
    for lines in point_lines:
        height += len(lines) * (body_font.size + 12) + LINE_GAP
    if summary.get("quote"):
        height += 26 + 26 + len(quote_lines) * (quote_font.size + 10) + 28
    height += 70 + PAD

    card = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle((0, 0, WIDTH - 1, height - 1), radius=28, fill=CARD)
    draw.rounded_rectangle((0, 0, 12, height - 1), radius=6, fill=ACCENT)

    y = PAD
    draw.text((PAD, y), "今日群聊总结", font=title_font, fill=TEXT)
    y += 58 + 8
    meta = f"{date_text} · {stat['messages']} 条消息 · {stat['users']} 人参与"
    if stat.get("span"):
        meta += f" · {stat['span']}"
    draw.text((PAD, y), meta, font=meta_font, fill=MUTED)
    y += 34 + 40

    for line in head_lines:
        draw.text((PAD, y), line, font=head_font, fill=ACCENT)
        y += head_font.size + 14
    y += 26

    for lines in point_lines:
        draw.text((PAD, y), "·", font=body_font, fill=ACCENT)
        for line in lines:
            draw.text((PAD + 34, y), line, font=body_font, fill=TEXT)
            y += body_font.size + 12
        y += LINE_GAP

    if quote_text:
        y += 26
        draw.rounded_rectangle((PAD, y, PAD + 6, y + 26 + len(quote_lines) * (quote_font.size + 10)), radius=3, fill=ACCENT)
        y += 4
        for line in quote_lines:
            draw.text((PAD + 26, y), line, font=quote_font, fill=MUTED)
            y += quote_font.size + 10

    draw.text((PAD, height - PAD - 26), "由露娜自动生成", font=footer_font, fill=MUTED)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / f"summary-{group_openid[:8]}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    card.save(target, "PNG", optimize=True)
    log(f"生成 {target.name} ({target.stat().st_size // 1024}KB)")
    return target


if __name__ == "__main__":
    import sys

    group = sys.argv[1] if len(sys.argv) > 1 else ""
    if not group:
        print("usage: python chat_summary.py <group_openid>")
        raise SystemExit(2)
    print(build(group))
