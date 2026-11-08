#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""露娜 /帮助 的 Markdown 菜单和按钮。"""

from __future__ import annotations

from urllib.parse import quote

BASE = "https://luna-site-d0gj36nib486137a4-1524033896.tcloudbaseapp.com"


def _cmd(text: str, show: str) -> str:
    """点击后把指令放进输入框，不直接发送。"""
    return (
        f'<qqbot-cmd-input text="{quote(text, safe="")}" '
        f'show="{quote(show, safe="")}" reference="false" />'
    )


def _button(button_id: str, label: str, url: str) -> dict:
    return {
        "id": button_id,
        "render_data": {"label": label, "visited_label": label, "style": 1},
        "action": {
            "type": 0,
            "permission": {"type": 2},
            "data": url,
            "unsupport_tips": "请升级 QQ",
        },
    }


def build() -> tuple[str, dict]:
    text = (
        "# 🍷 露娜菜单\n\n"
        "> 蓝色文字都能点：带链接图标的打开网页，不带的会把指令放进输入框\n\n"
        "**💬 聊天**\n"
        f"{_cmd('/露娜 ', '💬 聊天')}｜{_cmd('/露娜 ', '🎭 角色对话')}\n"
        "**🧰 功能**\n"
        f"{_cmd('/找图 ', '🔍 找图')}｜{_cmd('/q弹 ', '🖼️ Q弹')}｜"
        f"{_cmd('/聊天总结', '📝 聊天总结')}\n"
        f"{_cmd('/露娜 提醒我 ', '⏰ 提醒')}｜"
        f"{_cmd('/露娜 天气 ', '🌤️ 天气')}｜"
        f"{_cmd('/露娜 现在几点', '🕒 时间')}\n"
        f"{_cmd('/露娜 算一下 ', '🧮 算数')}｜"
        f"{_cmd('/露娜 B站 ', '📺 B站')}｜"
        f"{_cmd('/帮助', '❓ 帮助')}\n"
        "**📚 资料（带链接图标）**\n"
        f"[使用说明]({BASE}/manual.html)｜"
        f"[更新日志]({BASE}/changelog.html)｜"
        f"[Qtan]({BASE}/qtan.html)\n\n"
        "***\n"
        "露娜会潜水接话，也会在周末发频道。"
    )
    keyboard = {
        "content": {
            "rows": [
                {
                    "buttons": [
                        _button("b1", "使用说明", f"{BASE}/manual.html"),
                        _button("b2", "更新日志", f"{BASE}/changelog.html"),
                    ]
                },
                {
                    "buttons": [
                        _button("b3", "找图说明", f"{BASE}/manual.html#find"),
                        _button("b4", "Q弹说明", f"{BASE}/manual.html#qtan"),
                    ]
                },
                {
                    "buttons": [
                        _button("b5", "指令总表", f"{BASE}/manual.html#commands"),
                        _button("b6", "常见问题", f"{BASE}/manual.html#faq"),
                    ]
                },
                {
                    "buttons": [
                        _button("b7", "Qtan 工具", f"{BASE}/qtan.html"),
                        _button("b8", "官网首页", f"{BASE}/index.html"),
                    ]
                },
            ]
        }
    }
    return text, keyboard
