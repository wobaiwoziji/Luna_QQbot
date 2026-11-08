#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""露娜 /帮助 的 Markdown 菜单和按钮。"""

from __future__ import annotations

BASE = "https://luna-site-d0gj36nib486137a4-1524033896.tcloudbaseapp.com"


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
        "> 蓝色文字都可以点\n\n"
        "**💬 聊天**\n"
        f"[被 @ 或叫露娜]({BASE}/manual.html#talk)｜"
        f"[/露娜 角色对话]({BASE}/manual.html#talk)\n"
        "**🧰 功能**\n"
        f"[找图]({BASE}/manual.html#find)｜"
        f"[Q弹动图]({BASE}/manual.html#qtan)｜"
        f"[聊天总结]({BASE}/manual.html#summary)\n"
        f"[提醒]({BASE}/manual.html#reminder)｜"
        f"[小工具]({BASE}/manual.html#tools)｜"
        f"[指令总表]({BASE}/manual.html#commands)\n"
        "**📚 资料**\n"
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
