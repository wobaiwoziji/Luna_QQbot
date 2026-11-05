#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""渲染「帮助」指令的说明卡片。"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / "logs" / "help" / "help.png"

FONT_REGULAR = Path(r"C:\Windows\Fonts\msyh.ttc")
FONT_BOLD = Path(r"C:\Windows\Fonts\msyhbd.ttc")

WIDTH = 1000
PAD = 56
INNER = WIDTH - 2 * PAD

BG = (23, 24, 28)
CARD = (31, 32, 38)
TEXT = (245, 245, 247)
MUTED = (154, 154, 162)
ACCENT = (255, 107, 107)
LINE = (45, 46, 54)

TITLE = "露娜 使用说明"
SUBTITLE = "群里 @我 + 指令；聊天直接说就行"
ITEMS = [
    ("/露娜", [
        "用露娜的口吻聊天；问设定会联网查资料",
        "@某人 可以让露娜点评他 · 每人 20 秒",
    ]),
    ("/找图", [
        "角色名 / IP_角色名，可加 1 或 2",
        "例：/找图 角色名 2；/找图 随机图 · 每人 5 分钟",
    ]),
    ("/聊天总结", ["当日群聊要点卡片，至少 5 条消息", "每群 10 分钟"]),
    ("/q弹", ["发图片或引用图片 + /q弹", "每人 60 秒"]),
    ("提醒", [
        "「明天下午三点提醒我交表」",
        "到点在群里提醒 · 只有姐姐能用",
    ]),
    ("小工具", [
        "天气：北京天气；时间：现在几点",
        "算数：算一下 (3+5)*2",
    ]),
    ("记忆", [
        "会记得之前聊过的事",
        "问「还记得上次说的……」就行",
    ]),
    ("频道", [
        "周六日 09:00 / 16:00 发美图",
        "周六日 12:30 发梗图 · 自动配文",
    ]),
    ("/对着露娜发电", ["没人知道这么做会有什么下场，没人敢尝试"]),
    ("自动聊天（不用指令）", [
        "自己看群聊，感兴趣才插话；没兴趣就闭嘴",
        "会分享 B 站视频 · 每晚 22:30 道晚安",
    ]),
    ("/帮助", ["再看这份说明"]),
]
FOOTER = "官方接口读不到群成员头像，/q弹 请直接把图发给我。"


def _wrap(draw, text: str, font, width: int) -> list[str]:
    lines, current = [], ""
    for char in text:
        trial = current + char
        if draw.textlength(trial, font=font) <= width or not current:
            current = trial
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def build() -> Path:
    title_font = ImageFont.truetype(str(FONT_BOLD), 46)
    sub_font = ImageFont.truetype(str(FONT_REGULAR), 24)
    item_font = ImageFont.truetype(str(FONT_BOLD), 32)
    body_font = ImageFont.truetype(str(FONT_REGULAR), 27)
    foot_font = ImageFont.truetype(str(FONT_REGULAR), 21)

    probe = ImageDraw.Draw(Image.new("RGB", (WIDTH, 10)))
    blocks = []
    for name, lines in ITEMS:
        wrapped = []
        for line in lines:
            wrapped.extend(_wrap(probe, line, body_font, INNER - 24))
        blocks.append((name, wrapped))

    height = PAD + 46 + 12 + 24 + 34
    for _, wrapped in blocks:
        height += 32 + 14 + len(wrapped) * (27 + 12) + 24
    height += 24 + 21 + PAD + 12

    card = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle((0, 0, WIDTH - 1, height - 1), radius=28, fill=CARD)
    draw.rounded_rectangle((0, 0, 10, height - 1), radius=5, fill=ACCENT)

    x = PAD
    y = PAD
    draw.text((x, y), TITLE, font=title_font, fill=TEXT)
    y += 46 + 12
    draw.text((x, y), SUBTITLE, font=sub_font, fill=MUTED)
    y += 24 + 34

    for index, (name, wrapped) in enumerate(blocks):
        draw.text((x, y), name, font=item_font, fill=ACCENT)
        y += 32 + 14
        for line in wrapped:
            draw.text((x + 2, y), line, font=body_font, fill=TEXT)
            y += 27 + 12
        y += 24
        if index < len(blocks) - 1:
            draw.line((x, y - 14, WIDTH - PAD, y - 14), fill=LINE, width=1)

    y += 4
    for line in _wrap(probe, FOOTER, foot_font, INNER):
        draw.text((x, y), line, font=foot_font, fill=MUTED)
        y += 21 + 6

    OUT.parent.mkdir(parents=True, exist_ok=True)
    card.save(OUT, "PNG", optimize=True)
    return OUT


if __name__ == "__main__":
    print(build())
