#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve an IP/character name to booru tags and download the images.

Used by qq-sticker-bot.py. Sources are tried in order: pixiv.net -> safebooru.org
-> yande.re -> wallhaven.cc (wallhaven needs a proxy/VPN, so it is only a last resort).
"""

from __future__ import annotations

import hashlib
import io
import json
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import token_usage
TAG_MAP_PATH = HERE / "image-tags.json"
CACHE_DIR = ROOT / "picture" / "cache"
STATE_PATH = ROOT / "logs" / "image-bot-state.json"
SEARCH_CACHE_PATH = ROOT / "picture" / "search-cache.json"
LEARNED_PATH = ROOT / "picture" / "learned-tags.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
YANDE_TIMEOUT = 8
SAFEBOORU_TIMEOUT = 20
WALLHAVEN_TIMEOUT = 6
PIXIV_TIMEOUT = 15
PIXIV_REFERER = "https://www.pixiv.net/"
SEARCH_CACHE_TTL = 600
SEARCH_CACHE_LIMIT = 80
DOWNLOAD_TIMEOUT = 45
FETCH_BUDGET = 50
MAX_COUNT = 2
MAX_BYTES = 3 * 1024 * 1024
RANDOM_WORDS = ("随机图", "随机", "来点图", "随便来张")
MAX_EDGE = 1920
SEEN_LIMIT = 400
STATE_TAG_LIMIT = 200
PASS_THROUGH = re.compile(r"^[0-9a-zA-Z:_()\-. ]+$")


class ImageNotFound(RuntimeError):
    """No image could be found for the requested name."""


def log(message: str) -> None:
    print(f"[image] {message}", flush=True)


def load_tag_map() -> dict:
    data = json.loads(TAG_MAP_PATH.read_text(encoding="utf-8-sig"))
    for key in ("ips", "chars", "combos"):
        table = data.get(key) or {}
        data[key] = {str(name).strip().lower(): str(tag).strip() for name, tag in table.items()}
    data["aliases"] = build_aliases(data)
    return data


def build_aliases(tag_map: dict) -> dict:
    """Romaji shorthand: koishi -> komeiji_koishi, flandre -> flandre_scarlet.

    Ambiguous shorthands (e.g. "komeiji" for both Satori and Koishi) are dropped.
    """
    candidates: dict[str, set[str]] = {}
    for table in ("chars", "ips"):
        for tag in tag_map[table].values():
            base = tag.split("(")[0].strip().rstrip("_")
            parts = [part for part in base.split("_") if part]
            for alias in {parts[0], parts[-1]} if parts else []:
                if len(alias) < 4:
                    continue
                candidates.setdefault(alias, set()).add(tag)
    return {alias: next(iter(tags)) for alias, tags in candidates.items() if len(tags) == 1}


def lookup_part(part: str, tag_map: dict, passthrough: bool = True) -> str | None:
    key = part.strip().lower()
    if not key:
        return None
    for table in ("combos", "chars", "ips"):
        if key in tag_map[table]:
            return tag_map[table][key]
    if key in tag_map.get("aliases", {}):
        return tag_map["aliases"][key]
    if passthrough and PASS_THROUGH.match(key):
        return key.replace(" ", "_")
    return None


def resolve_tags(raw: str, tag_map: dict) -> str:
    name = (raw or "").strip()
    if not name:
        raise ImageNotFound("空名字")

    lowered = name.lower()
    if lowered in tag_map["combos"]:
        return tag_map["combos"][lowered]

    parts = [item for item in re.split(r"[\s_/]+", name) if item.strip()]
    if len(parts) > 1:
        resolved = [lookup_part(item, tag_map, passthrough=False) for item in parts]
        if all(resolved):
            return " ".join(dict.fromkeys(resolved))

    parts = [item for item in re.split(r"[_/]+", name) if item.strip()]
    if len(parts) > 1:
        resolved = [lookup_part(item, tag_map, passthrough=False) for item in parts]
        if all(resolved):
            return " ".join(dict.fromkeys(resolved))

    single = lookup_part(name, tag_map)
    if single:
        return single
    raise ImageNotFound(name)


def http_get(url: str, params: dict | None = None, timeout: int = 20, referer: str | None = None):
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer
    return requests.get(url, params=params, headers=headers, timeout=timeout)


def to_candidate(source: str, item_id, url: str | None, preview: str | None, score=0, prefer_preview: bool = False, tags: str = "", width=0, height=0, referer: str | None = None) -> dict:
    return {
        "source": source,
        "id": str(item_id),
        "url": url,
        "preview": preview or url,
        "score": int(score or 0),
        "prefer_preview": prefer_preview,
        "tags": str(tags or ""),
        "width": int(width or 0),
        "height": int(height or 0),
        "referer": str(referer or ""),
    }


def search_yande(tags: str, limit: int = 60) -> list[dict]:
    response = http_get(
        "https://yande.re/post.json",
        {"tags": f"{tags} rating:safe", "limit": limit},
        timeout=(YANDE_TIMEOUT, 15),
        referer="https://yande.re/",
    )
    response.raise_for_status()
    items = response.json() or []
    return [
        to_candidate("yande", item.get("id"), item.get("file_url"), item.get("sample_url") or item.get("jpeg_url"), item.get("score"), prefer_preview=True, tags=item.get("tags"), width=item.get("width"), height=item.get("height"))
        for item in items
    ]


def search_safebooru(tags: str, limit: int = 40) -> list[dict]:
    response = http_get(
        "https://safebooru.org/index.php",
        {
            "page": "dapi",
            "s": "post",
            "q": "index",
            "json": "1",
            "limit": limit,
            "tags": f"{tags} rating:safe",
        },
        timeout=(8, SAFEBOORU_TIMEOUT),
    )
    response.raise_for_status()
    try:
        items = response.json()
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    return [
        to_candidate("safebooru", item.get("id"), item.get("file_url"), item.get("sample_url"), item.get("score"), tags=item.get("tags"), width=item.get("width"), height=item.get("height"))
        for item in items
    ]


def search_wallhaven(tags: str, limit: int = 24) -> list[dict]:
    response = http_get(
        "https://wallhaven.cc/api/v1/search",
        {
            "q": tags.replace("_", " "),
            "categories": "010",
            "purity": "100",
            "sorting": "random",
            "atleast": "1600x900",
        },
        timeout=(WALLHAVEN_TIMEOUT, 10),
    )
    response.raise_for_status()
    payload = response.json() or {}
    return [
        to_candidate("wallhaven", item.get("id"), item.get("path"), item.get("thumbs", {}).get("large"), 0)
        for item in payload.get("data") or []
    ]


def search_pixiv(tags: str, limit: int = 40) -> list[dict]:
    keyword = (tags or "").strip()
    if not keyword:
        return []
    response = http_get(
        f"https://www.pixiv.net/ajax/search/artworks/{quote(keyword)}",
        {
            "word": keyword,
            "order": "popular_d",
            "mode": "safe",
            "p": 1,
            "s_mode": "s_tag_full",
            "type": "all",
            "lang": "zh",
        },
        timeout=(8, PIXIV_TIMEOUT),
        referer=PIXIV_REFERER,
    )
    response.raise_for_status()
    payload = response.json() or {}
    if payload.get("error"):
        raise RuntimeError(str(payload.get("message") or "pixiv search failed"))
    body = payload.get("body") or {}
    items = (
        (body.get("illustManga") or {}).get("data")
        or (body.get("illust") or {}).get("data")
        or []
    )
    candidates = []
    for item in items[:limit]:
        if int(item.get("xRestrict") or 0) != 0:
            continue
        raw_url = str(item.get("url") or "").strip()
        if not raw_url:
            continue
        master = re.sub(r"/c/[^/]+/", "/", raw_url).replace("_square1200", "_master1200")
        candidates.append(
            to_candidate(
                "pixiv",
                item.get("id"),
                master,
                raw_url,
                item.get("bookmarkCount") or 0,
                prefer_preview=False,
                tags=" ".join(str(tag) for tag in (item.get("tags") or [])),
                width=item.get("width") or 0,
                height=item.get("height") or 0,
                referer=PIXIV_REFERER,
            )
        )
    return candidates


SOURCES = (
    ("pixiv", search_pixiv),
    ("safebooru", search_safebooru),
    ("yande", search_yande),
    ("wallhaven", search_wallhaven),
)

_TAG_INFO: dict[str, tuple[int, int] | None] = {}

SOURCE_INTERVAL = {"pixiv": 1.5, "safebooru": 1.5, "yande": 1.0, "wallhaven": 1.0}
_LAST_CALL: dict[str, float] = {}


def throttled(source: str, call):
    delay = SOURCE_INTERVAL.get(source, 0.0) - (time.time() - _LAST_CALL.get(source, 0.0))
    if delay > 0:
        time.sleep(delay)
    _LAST_CALL[source] = time.time()
    return call()


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_cache() -> dict:
    return load_json(SEARCH_CACHE_PATH)


def cache_get(cache: dict, tag_key: str, source: str) -> list[dict] | None:
    entry = (cache.get(tag_key) or {}).get(source)
    if not isinstance(entry, dict) or time.time() - float(entry.get("ts", 0)) > SEARCH_CACHE_TTL:
        return None
    items = entry.get("items")
    return items if isinstance(items, list) else None


def cache_put(cache: dict, tag_key: str, source: str, items: list[dict]) -> None:
    now = time.time()
    bucket = cache.setdefault(tag_key, {})
    bucket[source] = {"ts": now, "items": items[:60]}
    if len(cache) > SEARCH_CACHE_LIMIT:
        for key in sorted(cache, key=lambda name: max((item.get("ts", 0) for item in cache[name].values()), default=0))[:-SEARCH_CACHE_LIMIT]:
            cache.pop(key, None)
    SEARCH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEARCH_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def tag_info(tag: str):
    """Look up a tag on safebooru: (type, count). 3=copyright, 4=character."""
    if tag in _TAG_INFO:
        return _TAG_INFO[tag]
    info = None
    try:
        response = http_get(
            "https://safebooru.org/index.php",
            {"page": "dapi", "s": "tag", "q": "index", "json": "1", "name": tag},
            timeout=(6, 12),
        )
        text = response.text or ""
        matched = re.search(r'<tag type="(\d+)" count="(\d+)"', text)
        if matched:
            info = (int(matched.group(1)), int(matched.group(2)))
        elif text.lstrip().startswith("<?xml") or text.lstrip().startswith("<tag"):
            info = (0, 0)
        elif text.strip().startswith("{"):
            data = json.loads(text)
            items = data if isinstance(data, list) else [data]
            if items and isinstance(items[0], dict):
                info = (int(items[0].get("type", 0)), int(items[0].get("count", 0)))
    except Exception as exc:
        log(f"tag info failed {tag}: {exc}")
    _TAG_INFO[tag] = info
    return info


def validate_tags(tags: str) -> None:
    for tag in tags.split():
        info = tag_info(tag)
        if not info:
            continue
        tag_type, count = info
        if tag_type == 0 and count == 0:
            raise ImageNotFound(f"「{tag}」这个 tag 不存在，换个名字试试")
        if tag_type in (3, 4) or count >= 1000:
            continue
        raise ImageNotFound(f"「{tag}」不是作品或角色 tag（类型 {tag_type}，仅 {count} 张），换个名字试试")


def load_state() -> dict:
    return load_json(STATE_PATH)


def load_learned() -> dict:
    data = load_json(LEARNED_PATH)
    return {str(key).strip().lower(): str(value).strip() for key, value in data.items() if value}


def save_learned(name: str, tags: str) -> None:
    data = load_learned()
    data[name.strip().lower()] = tags
    if len(data) > 500:
        data = dict(list(data.items())[-500:])
    LEARNED_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEARNED_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def tag_valid(tag: str) -> bool:
    info = tag_info(tag)
    if not info:
        return False
    tag_type, count = info
    return tag_type in (3, 4) or count >= 1000


def llm_resolve(raw: str) -> str | None:
    """Ask DeepSeek to translate a Chinese name into booru tags, then verify them."""
    try:
        import chat_summary
        key = chat_summary.api_key()
    except Exception:
        key = ""
    if not key:
        return None
    prompt = (
        "把下面的中文动漫/游戏作品名或角色名，转换成 safebooru/gelbooru 的英文 tag（小写、下划线）。"
        "如果是角色，必须给出**角色 tag**（一般形如 name_(series)）；作品 tag 可附带在后面。"
        "只输出 JSON：{\"tags\": [\"候选1\", \"候选2\"]}，按可能性排序，最多 3 个。"
        "只给作品 tag（例如只有 arknights）是不合格的；不确定角色就返回 {\"tags\": []}。\n"
        f"输入：{raw}"
    )
    try:
        response = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": "你熟悉 danbooru/gelbooru 的角色 tag，只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 200,
                "response_format": {"type": "json_object"},
            },
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        token_usage.from_response("findimage", "deepseek-chat", data)
        parsed = json.loads(data["choices"][0]["message"]["content"])
        candidates = [str(item).strip() for item in parsed.get("tags") or [] if str(item).strip()]
    except Exception as exc:
        log(f"llm resolve failed {raw}: {exc}")
        return None

    wants_character = "_" in raw or len(raw) <= 6
    for candidate in candidates:
        parts = candidate.split()
        infos = [(part, tag_info(part)) for part in parts]
        if not parts or any(not info or (info[0] not in (3, 4) and info[1] < 1000) for _, info in infos):
            log(f"llm candidate rejected (tag) {raw} -> {candidate}")
            continue
        has_character = any(info and info[0] == 4 for _, info in infos)
        if wants_character and not has_character:
            log(f"llm candidate rejected (no character) {raw} -> {candidate}")
            continue
        log(f"llm resolve {raw} -> {candidate}")
        return candidate
    return None


def learn_resolve(raw: str) -> str:
    key = raw.strip().lower()
    learned = load_learned()
    if key in learned:
        return learned[key]
    tags = llm_resolve(raw)
    if not tags:
        raise ImageNotFound(raw)
    save_learned(key, tags)
    return tags

def save_state(state: dict) -> None:
    seen = state.get("seen") or {}
    if len(seen) > STATE_TAG_LIMIT:
        for key in list(seen)[:-STATE_TAG_LIMIT]:
            seen.pop(key, None)
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def seen_ids(state: dict, tag_key: str) -> set[str]:
    return {str(item) for item in (state.get("seen") or {}).get(tag_key, [])}


def mark_seen(state: dict, tag_key: str, ids: list[str]) -> None:
    bucket = (state.setdefault("seen", {})).setdefault(tag_key, [])
    for item in ids:
        if item not in bucket:
            bucket.append(item)
    del bucket[:-SEEN_LIMIT]


def encode_under_limit(image: Image.Image, max_bytes: int) -> bytes:
    work = image
    data = b""
    while True:
        for quality in (90, 85, 80, 72, 64, 55, 45, 35):
            buffer = io.BytesIO()
            work.save(buffer, "JPEG", quality=quality, optimize=True)
            data = buffer.getvalue()
            if len(data) <= max_bytes:
                return data
        if max(work.size) <= 480:
            return data
        work = work.resize((max(1, int(work.width * 0.85)), max(1, int(work.height * 0.85))), Image.LANCZOS)


def save_image(raw: bytes, candidate: dict) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / f"{candidate['source']}-{candidate['id']}.jpg"
    if target.exists() and 0 < target.stat().st_size <= MAX_BYTES:
        return target

    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:
        raise RuntimeError(f"图片解码失败: {exc}") from exc

    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        canvas = Image.new("RGB", image.size, (255, 255, 255))
        canvas.paste(image, mask=image.split()[-1])
        image = canvas
    else:
        image = image.convert("RGB")

    if max(image.size) > MAX_EDGE:
        image.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)

    target.write_bytes(encode_under_limit(image, MAX_BYTES))
    return target


def combine_images(paths: list[Path], gap: int = 16, max_height: int = 6000) -> Path:
    images = []
    for path in paths:
        with Image.open(path) as source:
            images.append(source.convert("RGB"))
    width = min(max(image.width for image in images), 1600)
    scaled = []
    for image in images:
        if image.width == width:
            scaled.append(image)
            continue
        height = max(1, round(image.height * width / image.width))
        scaled.append(image.resize((width, height), Image.LANCZOS))

    total = sum(image.height for image in scaled) + gap * (len(scaled) - 1)
    if total > max_height:
        ratio = max_height / total
        scaled = [
            image.resize((max(1, round(image.width * ratio)), max(1, round(image.height * ratio))), Image.LANCZOS)
            for image in scaled
        ]
        gap = max(4, round(gap * ratio))
        total = sum(image.height for image in scaled) + gap * (len(scaled) - 1)

    canvas = Image.new("RGB", (width, total), (255, 255, 255))
    top = 0
    for image in scaled:
        canvas.paste(image, (0, top))
        top += image.height + gap

    key = "-".join(path.stem for path in paths)
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()[:10]
    target = CACHE_DIR / f"collage-{digest}.jpg"
    target.write_bytes(encode_under_limit(canvas, MAX_BYTES))
    return target


def download_image(candidate: dict, deadline: float) -> Path:
    last_error = None
    order = (candidate.get("preview"), candidate.get("url")) if candidate.get("prefer_preview") else (candidate.get("url"), candidate.get("preview"))
    urls = [item for item in dict.fromkeys(order) if item]
    for url in urls:
        if time.time() > deadline:
            break
        try:
            started = time.time()
            headers = {"User-Agent": USER_AGENT}
            if candidate.get("referer"):
                headers["Referer"] = str(candidate["referer"])
            with requests.get(url, headers=headers, timeout=(8, 15), stream=True) as response:
                response.raise_for_status()
                buffer = bytearray()
                for chunk in response.iter_content(65536):
                    buffer.extend(chunk)
                    if time.time() - started > 25:
                        raise TimeoutError("下载超过 25 秒")
            return save_image(bytes(buffer), candidate)
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"下载失败: {last_error}")


def tags_match(candidate: dict, char_tags: list[str]) -> bool:
    if candidate.get("source") == "pixiv":
        return True
    if not char_tags:
        return True
    post_tags = (candidate.get("tags") or "").split()
    if not post_tags:
        return True
    return all(tag in post_tags for tag in char_tags)


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text or ""))


def choose_picks(pools: list[list[dict]], count: int) -> list[dict]:
    for pool in pools:
        pool.sort(key=lambda item: item.get("score", 0), reverse=True)
    picks: list[dict] = []
    if pools:
        pool = pools[0]
        big = [item for item in pool if max(item.get("width", 0), item.get("height", 0)) >= 1000]
        if len(big) >= count:
            pool = big
        ranked = sorted(pool, key=lambda item: (item.get("score", 0), item.get("width", 0)), reverse=True)[:20]
        picks = random.sample(ranked, min(count, len(ranked)))
    flat = [item for pool in pools for item in pool]
    if len(picks) < count:
        spare = [item for item in flat if item not in picks]
        random.shuffle(spare)
        picks = picks + spare[: count - len(picks)]
    return picks


def collect_candidates(
    tags: str,
    tag_key: str,
    cache: dict,
    seen: set[str],
    errors: list[str],
    deadline: float,
    count: int,
    char_tags: list[str] | None = None,
) -> list[list[dict]]:
    per_source = []
    for name, search in SOURCES:
        items = cache_get(cache, tag_key, name)
        if items is None:
            if time.time() > deadline:
                errors.append(f"{name}: 跳过（超时）")
                continue
            last_error = None
            for attempt in range(2):
                try:
                    started = time.time()
                    items = throttled(name, lambda: search(tags))
                    log(f"search {name} tags={tags} items={len(items)} {time.time() - started:.1f}s")
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    time.sleep(0.8 + attempt)
            if last_error is not None:
                errors.append(f"{name}: {last_error}")
                continue
            cache_put(cache, tag_key, name, items)
        fresh = [item for item in items if item.get("url") and item["id"] not in seen and tags_match(item, char_tags or [])]
        if fresh:
            per_source.append(fresh)
            if len(fresh) >= count:
                break
    return per_source


def fetch_images(raw: str, count: int = 1, mark: bool = True, tags_override: str | None = None) -> tuple[list[Path], str]:
    if count < 1:
        count = 1
    if count > MAX_COUNT:
        raise ValueError(f"count must be <= {MAX_COUNT}")

    if not tags_override and (raw or "").strip() in RANDOM_WORDS:
        return fetch_random(count, mark=mark)

    if tags_override:
        tags = tags_override
    else:
        tag_map = load_tag_map()
        try:
            tags = resolve_tags(raw, tag_map)
        except ImageNotFound:
            try:
                tags = learn_resolve(raw)
            except ImageNotFound:
                if not _has_cjk(raw):
                    raise
                tags = raw.strip()
        if re.fullmatch(r"[ -~]+", (raw or "").strip()):
            validate_tags(tags)
    tag_key = tags.replace(" ", "+")
    state = load_state()
    cache = load_cache()
    errors: list[str] = []
    deadline = time.time() + FETCH_BUDGET

    queries = [tags]
    raw_name = (raw or "").strip()
    if raw_name and raw_name != tags and _has_cjk(raw_name) and not re.search(r"\s", raw_name):
        queries.insert(0, raw_name)

    def search_tags(query: str) -> tuple[list[dict], list[dict], str]:
        key = query.replace(" ", "+")
        query_seen = seen_ids(state, key)
        chars = [part for part in query.split() if (tag_info(part) or (0, 0))[0] == 4]
        pools = collect_candidates(query, key, cache, query_seen, errors, deadline, count, chars)
        chosen = choose_picks(pools, count)
        if not chosen and query_seen:
            log(f"{query} 已看完全部缓存，重置去重记录")
            state.setdefault("seen", {})[key] = []
            pools = collect_candidates(query, key, cache, set(), errors, deadline, count, chars)
            chosen = choose_picks(pools, count)
        return chosen, [item for pool in pools for item in pool], key

    picks: list[dict] = []
    flat: list[dict] = []
    for query in queries:
        picks, flat, tag_key = search_tags(query)
        if picks:
            if query != tags:
                log(f"image raw query={query!r}")
                tags = query
            break
    if not picks:
        detail = "; ".join(errors[:2])
        raise ImageNotFound(f"{raw}（tags={tags}）{detail}")

    queue = list(picks) + [item for item in flat if item not in picks][:8]
    paths = []
    used = []

    def try_download(candidate: dict):
        if time.time() > deadline:
            return None
        started = time.time()
        try:
            path = download_image(candidate, deadline)
        except Exception as exc:
            log(f"下载失败 {candidate['source']}#{candidate['id']}: {exc}")
            return None
        log(f"downloaded {candidate['source']}#{candidate['id']} {time.time() - started:.1f}s")
        return path, candidate["id"]

    batch = queue[:count]
    if batch:
        with ThreadPoolExecutor(max_workers=max(1, len(batch))) as executor:
            for outcome in executor.map(try_download, batch):
                if outcome:
                    paths.append(outcome[0])
                    used.append(outcome[1])

    for candidate in queue[count:]:
        if len(paths) >= count or time.time() > deadline:
            break
        outcome = try_download(candidate)
        if outcome:
            paths.append(outcome[0])
            used.append(outcome[1])

    if not paths:
        raise ImageNotFound(f"{raw}（tags={tags}）")

    if mark:
        mark_seen(state, tag_key, used)
        save_state(state)
    return paths, tags


def fetch_random(count: int = 1, mark: bool = True) -> tuple[list[Path], str]:
    tag_map = load_tag_map()
    pool = [tag for tag in set(tag_map["chars"].values()) if tag]
    if not pool:
        raise ImageNotFound("随机图池为空")
    random.shuffle(pool)
    last_error = None
    for tag in pool[:5]:
        try:
            return fetch_images(tag, count, mark=mark, tags_override=tag)
        except Exception as exc:
            last_error = exc
    raise ImageNotFound(f"随机图失败: {last_error}")


def main() -> int:
    args = [item for item in sys.argv[1:] if item != "--keep"]
    keep = "--keep" in sys.argv[1:]
    if not args:
        print("usage: python image_source.py <名字> [1|2] [--keep]")
        return 2
    count = 1
    if len(args) > 1 and args[-1].isdigit():
        count = int(args.pop())
    name = " ".join(args)
    try:
        paths, tags = fetch_images(name, count, mark=not keep)
    except Exception as exc:
        print(f"FAIL {exc}")
        return 1
    print(f"TAGS {tags}")
    for path in paths:
        print(f"OK {path} {path.stat().st_size // 1024}KB")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
