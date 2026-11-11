#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""露娜知识库：优先用「调用版」问答条目，命中不了再退回 Markdown 分节检索。"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
JSON_PATH = HERE / "luna-kb.json"
MD_PATH = HERE / "luna-kb.md"

MAX_ENTRIES = 2
MIN_SCORE = 3.0
MAX_CHARS = 1800

STOPWORDS = {
    "是什么", "什么", "是谁", "谁", "哪来", "怎么", "为什么", "关系", "介绍", "一下", "哪些",
    "怎样", "如何",
}

_cache: dict = {"mtime": 0.0, "entries": []}
_md_cache: dict = {"mtime": 0.0, "sections": [], "idf": {}, "grams": []}


def _grams(text: str) -> list[str]:
    cleaned = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", text or "")
    return [cleaned[i : i + 2] for i in range(len(cleaned) - 1)]


def _load_entries() -> list[dict]:
    try:
        mtime = JSON_PATH.stat().st_mtime
    except OSError:
        return []
    if _cache["mtime"] == mtime:
        return _cache["entries"]
    try:
        data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    entries = [item for item in data if isinstance(item, dict) and item.get("q")]
    _cache.update(mtime=mtime, entries=entries)
    return entries


def _score(entry: dict, query: str, query_grams: set[str], name: str = "") -> float:
    score = 0.0
    keywords = [str(item).strip() for item in (entry.get("keywords") or [])]
    for keyword in keywords:
        if len(keyword) < 2:
            if keyword and keyword in query and len(query) <= 8:
                score += 1.0
            continue
        if keyword in STOPWORDS:
            continue
        if keyword in query:
            score += 1.0 + 0.5 * min(len(keyword), 5)
    if name and name in keywords:
        score += 4.0
    question = str(entry.get("q") or "")
    question_grams = set(_grams(question))
    if question_grams:
        score += 6.0 * len(question_grams & query_grams) / len(question_grams)
    if question and (question in query or query in question):
        score += 2.0
    return score


def _lookup_entries(query: str) -> str:
    entries = _load_entries()
    if not entries:
        return ""
    chapter = re.search(r"(?:第\s*)?([0-9一二三四五六七八九十百]+)\s*章", query)
    if chapter:
        number = chapter.group(1)
        if number.isdigit() and int(number) > 44:
            return ""
        if not any(
            number in (str(item.get("q") or "") + "".join(map(str, item.get("keywords") or [])) + str(item.get("source") or ""))
            for item in entries
        ):
            return ""
    query_grams = set(_grams(query))
    matched = re.match(r"^([\u4e00-\u9fffA-Za-z0-9]{1,6}?)(?:是谁|是什么|在哪|怎么样|的过去)", query)
    name = matched.group(1) if matched else ""
    scored = sorted(
        ((_score(entry, query, query_grams, name), entry) for entry in entries),
        key=lambda pair: pair[0],
        reverse=True,
    )
    top_score, top = scored[0]
    if top_score < MIN_SCORE:
        return ""
    picked = [top]
    if len(scored) > 1 and scored[1][0] >= max(MIN_SCORE, top_score * 0.7):
        picked.append(scored[1][1])

    parts = []
    used = 0
    for entry in picked:
        block = f"【{entry.get('q')}】\n事实：{entry.get('fact')}\n露娜口径：{entry.get('voice')}"
        if used + len(block) > MAX_CHARS:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


# ----------------------------- Markdown 兜底 -----------------------------

def _split_md(raw: str) -> list[tuple[str, str]]:
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


def _load_md() -> None:
    try:
        mtime = MD_PATH.stat().st_mtime
    except OSError:
        return
    if _md_cache["mtime"] == mtime:
        return
    try:
        raw = MD_PATH.read_text(encoding="utf-8")
    except OSError:
        return
    sections = _split_md(raw)
    grams = [(set(_grams(title)), set(_grams(body))) for title, body in sections]
    total = len(sections) or 1
    df: dict[str, int] = {}
    for title_grams, body_grams in grams:
        for gram in title_grams | body_grams:
            df[gram] = df.get(gram, 0) + 1
    idf = {gram: math.log((total + 1) / (count + 1)) + 0.1 for gram, count in df.items()}
    _md_cache.update(mtime=mtime, sections=sections, grams=grams, idf=idf)


def _lookup_md(query: str) -> str:
    _load_md()
    sections = _md_cache["sections"]
    if not sections:
        return ""
    idf = _md_cache["idf"]
    query_grams = set(_grams(query))
    scored = []
    for (title, body), (title_grams, body_grams) in zip(sections, _md_cache["grams"]):
        score = 0.0
        for gram in query_grams:
            weight = idf.get(gram, 0.0)
            if gram in title_grams:
                score += weight * 2.5
            elif gram in body_grams:
                score += weight
        if score >= 1.5:
            scored.append((score, title, body))
    if not scored:
        return ""
    scored.sort(key=lambda item: item[0], reverse=True)
    parts = []
    used = 0
    for _, title, body in scored[:2]:
        chunk = body if len(body) <= 1200 else body[:1200].rstrip() + "……"
        block = f"【{title}】\n{chunk}"
        if used + len(block) > MAX_CHARS:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


def lookup(text: str) -> str:
    """返回知识库片段；优先问答条目，没有命中再退回 Markdown。"""
    query = (text or "").strip()
    if len(query) < 2:
        return ""
    return _lookup_entries(query) or _lookup_md(query)
