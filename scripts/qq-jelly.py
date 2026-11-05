#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把群友发来的图片做成 Q 弹 GIF（参数固定）。"""

import importlib.util
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JELLY_PATH = ROOT / "Qtan" / "jelly.py"
OUT_DIR = ROOT / "logs" / "qtan"

# .venv-qq 没装 numpy/scipy，借用基础解释器里已装的版本
_BASE_SITE = Path(sys.base_prefix) / "Lib" / "site-packages"
if _BASE_SITE.exists() and str(_BASE_SITE) not in sys.path:
    sys.path.append(str(_BASE_SITE))

# 用户确认的手感：上下伸缩 50 / 压扁回弹 260 / 节奏 3.0 / 摇摆 0 / 果冻 0
PRESET = dict(bounce=50, squash=260, speed=3.0, sway=0, jelly=0)
SIZE = 480
FPS = 25

TRIGGER = re.compile(r"[qQ]\s*弹")
IMAGE_SUFFIX = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif")
_module = None


def wanted(text: str) -> bool:
    return bool(TRIGGER.search(text or ""))


def _jelly():
    global _module
    if _module is None:
        spec = importlib.util.spec_from_file_location("qtan_jelly", JELLY_PATH)
        _module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_module)
    return _module


def image_urls(attachments) -> list[str]:
    urls = []
    for item in attachments or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        ctype = str(item.get("content_type") or "").lower()
        name = str(item.get("filename") or "").lower()
        if url and (ctype.startswith("image/") or name.endswith(IMAGE_SUFFIX)):
            urls.append(url)
    return urls


def deep_image_urls(payload) -> list[str]:
    """引用/转发消息里，图片藏在 msg_elements 里，递归找。"""
    if not isinstance(payload, dict):
        return []
    urls = image_urls(payload.get("attachments"))
    for element in payload.get("msg_elements") or []:
        urls.extend(deep_image_urls(element))
    return urls


def dump_event(data: dict) -> Path:
    """排查用：把引用消息的结构留下（去掉 token）。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    scene = dict(data.get("message_scene") or {})
    scene["ext"] = ["<redacted>"] if scene.get("ext") else []
    keep = {
        "content": data.get("content"),
        "message_type": data.get("message_type"),
        "message_scene": scene,
        "attachments": data.get("attachments"),
        "msg_elements": data.get("msg_elements"),
    }
    path = OUT_DIR / "last-event.json"
    path.write_text(json.dumps(keep, ensure_ascii=False, indent=1)[:20000], encoding="utf-8")
    return path


def _download(url: str, dest: Path) -> Path:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read()
    if not data:
        raise RuntimeError("图片下载为空")
    dest.write_bytes(data)
    return dest


def render(source: Path) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUT_DIR / f"qtan-{int(time.time() * 1000)}.gif"
    _jelly().export(source, output, size=SIZE, fps=FPS, bg="transparent", colors=256, **PRESET)
    return output


def generate(url: str) -> Path:
    stamp = f"{int(time.time() * 1000)}"
    source = _download(url, OUT_DIR / f"src-{stamp}.img")
    return render(source)


def prune(keep: int = 60) -> None:
    if not OUT_DIR.exists():
        return
    files = sorted(OUT_DIR.glob("*"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files[keep:]:
        try:
            path.unlink()
        except OSError:
            pass
