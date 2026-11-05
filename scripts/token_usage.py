#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""记录 DeepSeek 调用的 token 消耗，供工作台统计展示。"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "logs" / "token-usage.jsonl"
MAX_BYTES = 4 * 1024 * 1024
MAX_LINES = 20000

_lock = threading.Lock()


def record(source: str, model: str, usage, images: int = 0) -> None:
    """一次调用记一行。usage 缺失（接口未返回）时静默跳过。"""
    if not isinstance(usage, dict):
        return
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or (prompt + completion))
    if total <= 0:
        return

    entry = {
        "ts": int(time.time()),
        "source": str(source or "other"),
        "model": str(model or ""),
        "prompt": prompt,
        "completion": completion,
        "total": total,
    }
    if images:
        entry["images"] = int(images)

    with _lock:
        try:
            PATH.parent.mkdir(parents=True, exist_ok=True)
            with PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            _shrink()
        except Exception:
            pass


def from_response(source: str, model: str, payload, images: int = 0) -> None:
    """从 DeepSeek 响应体里取 usage 并记录。"""
    if not isinstance(payload, dict):
        return
    record(source, model, payload.get("usage"), images)


def _shrink() -> None:
    try:
        if PATH.stat().st_size <= MAX_BYTES:
            return
        lines = PATH.read_text(encoding="utf-8").splitlines()[-MAX_LINES:]
        PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass
