#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""以图搜图：用 SauceNAO 反查图片出处（Pixiv / Twitter / 图站）。"""

from __future__ import annotations

import html as html_lib
import re
import time
from pathlib import Path

import requests

HOME = "https://saucenao.com/"
SEARCH = "https://saucenao.com/search.php"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
SOURCE_HINTS = ("pixiv.net", "twitter.com", "x.com", "danbooru", "gelbooru", "yande.re", "mangadex", "artstation", "deviantart")
MAX_BYTES = 8 * 1024 * 1024


def log(text: str) -> None:
    print(f"[reverse] {text}", flush=True)


def _clean(raw: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw or "")
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def download(url: str, dest: Path) -> Path:
    response = requests.get(url, headers=HEADERS, timeout=(8, 20), stream=True)
    response.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with dest.open("wb") as handle:
        for chunk in response.iter_content(65536):
            total += len(chunk)
            if total > MAX_BYTES:
                raise RuntimeError("图片太大")
            handle.write(chunk)
    if total == 0:
        raise RuntimeError("图片为空")
    return dest


def _pick_link(chunk: str) -> str:
    links = re.findall(r'href="(https?://[^"]+)"', chunk)
    for link in links:
        link = html_lib.unescape(link)
        if "saucenao.com" in link:
            continue
        if any(hint in link for hint in SOURCE_HINTS):
            return link
    return ""


def search(path: Path, limit: int = 4) -> list[dict]:
    session = requests.Session()
    try:
        session.get(HOME, headers=HEADERS, timeout=15)
    except Exception:
        pass
    with path.open("rb") as handle:
        response = session.post(
            SEARCH,
            files={"file": (path.name or "image.jpg", handle, "image/jpeg")},
            headers={**HEADERS, "Referer": HOME},
            timeout=(8, 45),
        )
    if response.status_code != 200:
        raise RuntimeError(f"saucenao {response.status_code}")
    body = response.text
    if "Just a moment" in body or "Result hidden" in body and "resulttable" not in body:
        raise RuntimeError("saucenao blocked")

    chunks = re.split(r'<div class="result(?: hidden)?"', body)[1:]
    results = []
    for chunk in chunks:
        matched = re.search(r'resultsimilarityinfo">([\d.]+)%', chunk)
        if not matched:
            continue
        similarity = float(matched.group(1))
        title_match = re.search(r'<div class="resulttitle"><strong>(.*?)</strong>', chunk, re.S)
        title = _clean(title_match.group(1)) if title_match else ""
        url = _pick_link(chunk)
        if not url:
            continue
        source = "Pixiv" if "pixiv.net" in url else "Twitter" if ("twitter.com" in url or "x.com" in url) else "图站"
        results.append({"similarity": similarity, "title": title or "未命名", "url": url, "source": source})

    results.sort(key=lambda item: item["similarity"], reverse=True)
    return results[:limit]


def format_results(results: list[dict]) -> str:
    lines = []
    for index, item in enumerate(results, 1):
        lines.append(f"{index}. {item['title']}（{item['source']}，相似度 {item['similarity']:.1f}%）\n{item['url']}")
    return "\n\n".join(lines)
