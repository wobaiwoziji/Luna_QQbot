#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""角色身份卡：按《战双帕弥什》的设定给群友分配身份，回复时按身份调整称呼与语气。

内置身份库在 scripts/luna-roles.default.json（可公开）。
群成员分配和自定义身份在 logs/luna-roles.json（只在本地）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = Path(__file__).resolve().parent / "luna-roles.default.json"
CUSTOM_PATH = ROOT / "logs" / "luna-roles.json"
GROUPS_PATH = ROOT / "logs" / "groups.json"
CHAT_DIR = ROOT / "logs" / "chat"

_cache: dict[str, tuple[float, dict]] = {}


def log(text: str) -> None:
    print(f"[roles] {text}", flush=True)


def _read(path: Path) -> dict:
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return {}
    cached = _cache.get(str(path))
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data = data if isinstance(data, dict) else {}
    except Exception:
        data = {}
    _cache[str(path)] = (stamp, data)
    return data


def all_roles() -> dict[str, dict]:
    """内置身份 + 自定义身份，同 id 时自定义覆盖内置。"""
    roles: dict[str, dict] = {}
    for source in (_read(DEFAULT_PATH), _read(CUSTOM_PATH)):
        items = source.get("roles")
        if not isinstance(items, dict):
            continue
        for role_id, item in items.items():
            if not isinstance(item, dict):
                continue
            role = dict(item)
            role["id"] = str(role_id)
            roles[str(role_id)] = role
    return roles


def state() -> dict:
    data = _read(CUSTOM_PATH)
    return {
        "roles": data.get("roles") if isinstance(data.get("roles"), dict) else {},
        "assign": data.get("assign") if isinstance(data.get("assign"), dict) else {},
    }


def _assignment(group: str, user: str) -> dict:
    if not group or not user:
        return {}
    entry = (state()["assign"].get(group) or {}).get(user)
    return entry if isinstance(entry, dict) else {}


def resolve(group: str, user: str, sister_openid: str = "") -> dict:
    """返回这个人在这个群的身份；没配置返回空 dict。"""
    if not user:
        return {}
    roles = all_roles()
    if sister_openid and user == str(sister_openid).strip():
        return roles.get("alpha") or {
            "id": "alpha",
            "name": "阿尔法",
            "tier": "sister",
            "privileged": True,
        }
    entry = _assignment(group, user)
    role_id = str(entry.get("role") or "").strip()
    if role_id == "alpha":
        return {}
    if role_id and role_id in roles:
        return roles[role_id]
    return {}


def is_sister(role: dict) -> bool:
    return str((role or {}).get("tier") or "") == "sister"


def is_privileged(role: dict) -> bool:
    return bool((role or {}).get("privileged"))


def display(role: dict) -> str:
    return str((role or {}).get("name") or "").strip()


def card_text(role: dict) -> str:
    """身份卡注入到提示词的文本；没有身份返回空串。"""
    if not role:
        return ""
    name = display(role)
    text = str(role.get("prompt") or "").strip()
    if not text:
        note = str(role.get("note") or "").strip()
        text = f"【对方身份】说话的人是{name}。{note}"
    return "\n\n" + text


def identity_line(role: dict) -> str:
    if is_sister(role):
        return "……姐姐。这种问题不用问，我认得你。"
    name = display(role) or "群友"
    return f"……{name}。这种问题不用问，我认得你。"


def target_card_text(role: dict) -> str:
    """被 @ 的人的身份卡：把「说话的人」改成「被 @ 的人」。"""
    text = card_text(role)
    return text.replace("说话的人是", "被 @ 的人是") if text else ""


def _known_groups() -> list[str]:
    groups: list[str] = []
    try:
        registry = json.loads(GROUPS_PATH.read_text(encoding="utf-8"))
        if isinstance(registry, dict):
            groups.extend(str(key) for key in registry if key)
    except Exception:
        pass
    if CHAT_DIR.exists():
        groups.extend(folder.name for folder in CHAT_DIR.iterdir() if folder.is_dir())
    return list(dict.fromkeys(groups))


def seed(sister_openid: str, groups: list[str] | None = None) -> int:
    """把姐姐在所有已知群里登记为阿尔法，其余人不受影响。"""
    sister = str(sister_openid or "").strip()
    if not sister:
        return 0
    data = _read(CUSTOM_PATH)
    data = json.loads(json.dumps(data)) if data else {}
    assign = data.setdefault("assign", {})
    if not isinstance(assign, dict):
        assign = data["assign"] = {}
    added = 0
    for group in groups or _known_groups():
        entry = assign.setdefault(group, {})
        if not isinstance(entry, dict):
            entry = assign[group] = {}
        if (entry.get(sister) or {}).get("role") == "alpha":
            continue
        entry[sister] = {"role": "alpha", "at": int(time.time())}
        added += 1
    if not added:
        return 0
    try:
        CUSTOM_PATH.parent.mkdir(parents=True, exist_ok=True)
        CUSTOM_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        log(f"seed save failed: {exc}")
        return 0
    return added
