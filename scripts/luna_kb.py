#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""露娜知识库：按问题挑出最相关的几节 Markdown 记录。"""

from __future__ import annotations

import math
import re
from pathlib import Path

KB_PATH = Path(__file__).resolve().parent / "luna-kb.md"
MAX_CHARS = 3000
MAX_SECTIONS = 4
MAX_SECTION_CHARS = 1500
MIN_SCORE = 0.8
LONG_SECTION_PENALTY = 0.6

_cache: dict = {"mtime": 0.0, "sections": [], "idf": {}}


def _bigrams(text: str) -> list[str]:
    cleaned = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", text or "")
    return [cleaned[i : i + 2] for i in range(len(cleaned) - 1)]


def _split(raw: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    title = ""
    body: list[str] = []
    for line in raw.splitlines():
        level = 3 if line.startswith("### ") else (2 if line.startswith("## ") else 0)
        if level:
            if body:
                sections.append((title, "\n".join(body).strip()))
            title = line[level + 1 :].strip()
            body = []
        elif line.strip() != "---":
            body.append(line)
    if body:
        sections.append((title, "\n".join(body).strip()))
    return [(title, text) for title, text in sections if text]


def _load() -> None:
    try:
        mtime = KB_PATH.stat().st_mtime
    except OSError:
        return
    if _cache["mtime"] == mtime:
        return
    try:
        raw = KB_PATH.read_text(encoding="utf-8")
    except OSError:
        return

    sections = _split(raw)
    grams = [(set(_bigrams(title)), set(_bigrams(body))) for title, body in sections]
    names = [set(re.findall(r"\*\*([^*\n]{1,20})\*\*", body)) for _, body in sections]
    total = len(sections) or 1
    df: dict[str, int] = {}
    for title_grams, body_grams in grams:
        for gram in title_grams | body_grams:
            df[gram] = df.get(gram, 0) + 1
    idf = {gram: math.log((total + 1) / (count + 1)) + 0.1 for gram, count in df.items()}

    _cache.update(mtime=mtime, sections=sections, grams=grams, idf=idf, names=names)


def lookup(text: str) -> str:
    """返回拼好的知识库片段；没有相关记录时返回空串。"""
    query = (text or "").strip()
    if len(query) < 2:
        return ""
    _load()
    sections = _cache["sections"]
    if not sections:
        return ""

    idf = _cache["idf"]
    grams = _cache["grams"]
    names = _cache.get("names") or [set() for _ in sections]
    query_grams = set(_bigrams(query))
    scored: list[tuple[float, int, str, str]] = []
    for (title, body), (title_grams, body_grams), section_names in zip(sections, grams, names):
        score = 0.0
        for gram in query_grams:
            weight = idf.get(gram, 0.0)
            if gram in title_grams:
                score += weight * 2.5
            elif gram in body_grams:
                score += weight
        if any(name in query for name in section_names):
            score += 1.5
        if score < MIN_SCORE:
            continue
        if len(body) > MAX_SECTION_CHARS:
            score *= LONG_SECTION_PENALTY
        scored.append((score, len(body), title, body))
    if not scored:
        return ""

    scored.sort(key=lambda item: item[0], reverse=True)
    parts: list[str] = []
    used = 0
    for _, _, title, body in scored[:MAX_SECTIONS]:
        chunk = body if len(body) <= MAX_SECTION_CHARS else body[:MAX_SECTION_CHARS].rstrip() + "……"
        block = f"【{title}】\n{chunk}"
        if used + len(block) > MAX_CHARS:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)
