#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""频道发帖：周六日美图 / 梗图，自动配文、转存图片、评论别人的帖子。"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path

import requests

import luna_chat
import qq_forum
import token_usage

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MEMES_DIR = ROOT / "picture" / "memes"
USED_DIR = MEMES_DIR / "used"
IMAGE_DIR = ROOT / "logs" / "luna-channel-images"
STATE_PATH = ROOT / "logs" / "luna-channel.json"
CHANNEL_CONFIG_PATH = ROOT / "logs" / "channel-config.json"

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
WEEKEND = (5, 6)
MAX_SEEN = 240

SLOTS = {
    "morning": {"start": 9 * 60, "end": 10 * 60, "kind": "image", "greeting": "上午好"},
    "noon": {"start": 12 * 60 + 30, "end": 13 * 60 + 30, "kind": "meme", "greeting": "中午好"},
    "afternoon": {"start": 16 * 60, "end": 17 * 60, "kind": "image", "greeting": "下午好"},
}
COMMENT_START = 19 * 60
COMMENT_END = 20 * 60
COMMENT_BLOCK_SECONDS = 7 * 86400

MEME_QUERIES = (
    "沙雕图",
    "熊猫头 表情包",
    "搞笑 梗图",
    "猫咪 梗图",
    "打工人 表情包",
)

STYLE_NOTE = (
    "\n\n【频道发帖】你在 QQ 频道发帖，不是在群里聊天。保持人设：冷淡、短句、不卖萌、不解释；"
    "不要用「人类」「蝼蚁」称呼频道成员；不聊政治、色情、暴力、真实人物；"
    "不提 AI、模型、提示词。标题最多 30 字，正文最多 80 字，不要加链接。"
)


def log(message: str) -> None:
    print(f"[channel] {message}", flush=True)


def load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as exc:
        log(f"state save failed: {exc}")


def config() -> dict:
    return qq_forum.load_config()


def config_channels() -> dict:
    cfg = config()
    comment_ids = cfg.get("forumCommentChannelIds")
    if not isinstance(comment_ids, list):
        comment_ids = []
    return {
        "image": str(cfg.get("forumImageChannelId", "")).strip(),
        "meme": str(cfg.get("forumMemeChannelId", "")).strip(),
        "comment": [str(item).strip() for item in comment_ids if str(item).strip()],
    }


def is_weekend(now: datetime) -> bool:
    return now.weekday() in WEEKEND


def minutes_of(now: datetime) -> int:
    return now.hour * 60 + now.minute


def slot_due(slot: str, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    conf = SLOTS[slot]
    return is_weekend(now) and conf["start"] <= minutes_of(now) < conf["end"]


def comment_due(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    return is_weekend(now) and COMMENT_START <= minutes_of(now) < COMMENT_END


def slot_key(slot: str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"{now:%Y-%m-%d}:{slot}"


def normalize_image(source: Path) -> Path:
    """统一成 1280 内的 JPEG，避免超大图被 QQ 拒绝。"""
    from PIL import Image

    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    target = IMAGE_DIR / f"{hashlib.sha1(source.read_bytes()).hexdigest()[:16]}.jpg"
    if target.exists():
        return target
    image = Image.open(source).convert("RGB")
    image.thumbnail((1280, 1280), Image.LANCZOS)
    image.save(target, "JPEG", quality=86)
    return target


def download(url: str, referer: str = "") -> Path:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    if referer:
        headers["Referer"] = referer
    response = requests.get(url, headers=headers, timeout=45)
    response.raise_for_status()
    if len(response.content) < 12_000:
        raise ValueError("图片太小")
    path = IMAGE_DIR / f"download-{hashlib.sha1(response.content).hexdigest()[:16]}.img"
    path.write_bytes(response.content)
    return path


def pick_meme_file() -> Path | None:
    MEMES_DIR.mkdir(parents=True, exist_ok=True)
    files = [
        item
        for item in MEMES_DIR.iterdir()
        if item.is_file() and item.suffix.lower() in IMAGE_EXT
    ]
    return random.choice(files) if files else None


def load_channel_settings() -> dict:
    try:
        data = json.loads(CHANNEL_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    urls = []
    for item in data.get("urls") or []:
        text = str(item or "").strip()
        if text:
            urls.append(text)
    return {"urls": urls[:50], "preferLocal": data.get("preferLocal", True) is not False}


def local_acg(seen: set[str]) -> dict | None:
    folder = ROOT / "picture" / "acg"
    if not folder.exists():
        return None
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT]
    random.shuffle(files)
    for path in files:
        try:
            if image_bytes_sha(path) in seen:
                continue
        except Exception:
            continue
        author = ""
        try:
            sidecar = path.with_suffix(path.suffix + ".json")
            if sidecar.exists():
                author = str(json.loads(sidecar.read_text(encoding="utf-8")).get("author") or "")
        except Exception:
            pass
        return {"path": path, "author": author, "source": f"local:{path.name}", "tags": ""}
    return None


def fetch_acg(seen: set[str] | None = None) -> dict:
    """优先自定义图源和本地图，再走 yande / konachan / safebooru。"""
    seen = set(seen or [])
    settings = load_channel_settings()

    custom = list(settings["urls"])
    random.shuffle(custom)
    for line in custom:
        url, _, author = line.partition("|")
        url = url.strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        try:
            path = download(url, url)
            if image_bytes_sha(path) in seen:
                continue
            return {"path": path, "author": author.strip(), "source": "custom", "tags": ""}
        except Exception as exc:
            log(f"custom image failed: {type(exc).__name__}: {exc}")

    if settings["preferLocal"]:
        picked = local_acg(seen)
        if picked:
            return picked

    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://yande.re/"}
    last = ""
    for attempt in range(4):
        try:
            response = requests.get(
                "https://yande.re/post.json",
                params={"tags": "rating:safe order:random", "limit": 40},
                headers=headers,
                timeout=25,
            )
            items = response.json()
            random.shuffle(items)
            for item in items:
                if not item.get("author") or not item.get("file_url"):
                    continue
                if item.get("file_ext") not in ("jpg", "jpeg", "png"):
                    continue
                if int(item.get("width") or 0) < 900:
                    continue
                path = download(item["file_url"], "https://yande.re/")
                return {
                    "path": path,
                    "author": str(item.get("author")),
                    "source": f"yande:{item.get('id')}",
                    "tags": str(item.get("tags") or ""),
                }
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(2)

    # yande 不通时退到 konachan.net，同样带 author
    try:
        response = requests.get(
            "https://konachan.net/post.json",
            params={"tags": "rating:safe order:random", "limit": 40},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://konachan.net/"},
            timeout=25,
        )
        items = response.json()
        random.shuffle(items)
        for item in items:
            if not item.get("author") or not item.get("file_url"):
                continue
            if item.get("file_ext") not in ("jpg", "jpeg", "png"):
                continue
            if int(item.get("width") or 0) < 900:
                continue
            path = download(item["file_url"], "https://konachan.net/")
            return {
                "path": path,
                "author": str(item.get("author")),
                "source": f"konachan:{item.get('id')}",
                "tags": str(item.get("tags") or ""),
            }
    except Exception as exc:
        last += f" | konachan: {type(exc).__name__}: {exc}"

    # 再退到 safebooru；作者信息可能缺失
    try:
        response = requests.get(
            "https://safebooru.org/index.php",
            params={
                "page": "dapi",
                "s": "post",
                "q": "index",
                "json": "1",
                "tags": "rating:safe order:random",
                "limit": 40,
            },
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://safebooru.org/"},
            timeout=30,
        )
        items = response.json()
        random.shuffle(items)
        for item in items:
            url = item.get("file_url") or item.get("sample_url")
            if not url or int(item.get("width") or 0) < 900:
                continue
            path = download(url, "https://safebooru.org/")
            return {
                "path": path,
                "author": "",
                "source": f"safebooru:{item.get('id')}",
                "tags": str(item.get("tags") or ""),
            }
    except Exception as exc:
        last += f" | safebooru: {type(exc).__name__}: {exc}"

    # 最后用本地备选图
    picked = local_acg(seen)
    if picked:
        return picked
    raise RuntimeError(f"美图抓取失败: {last}")


def fetch_meme() -> dict:
    """360 图搜中文梗图。"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://image.so.com/"}
    last = ""
    for query in random.sample(MEME_QUERIES, len(MEME_QUERIES)):
        for attempt in range(2):
            try:
                response = requests.get(
                    "https://image.so.com/j",
                    params={"q": query, "pn": str(random.randint(1, 8)), "rn": "12"},
                    headers=headers,
                    timeout=25,
                )
                items = response.json().get("list") or []
                random.shuffle(items)
                for item in items[:6]:
                    url = item.get("img") or item.get("thumb") or ""
                    if not url.startswith("http"):
                        continue
                    try:
                        path = download(url, "https://image.so.com/")
                    except Exception:
                        continue
                    return {"path": path, "author": "", "source": f"so360:{query}", "tags": query}
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
                time.sleep(1)
    raise RuntimeError(f"梗图抓取失败: {last}")


def image_bytes_sha(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def describe(path: Path, kind: str) -> dict:
    """看一次图，返回安全判定和配文素材。"""
    key = luna_chat.api_key()
    if not key:
        return {}
    try:
        part = luna_chat._image_part(path)
    except Exception as exc:
        log(f"image part failed: {exc}")
        return {}
    prompt = (
        "看这张图。只输出 JSON，不要解释，不要加代码块：\n"
        '{"safe": true, "what": "...", "character": "...", "quote": "...", "roast": "..."}\n'
        "safe：是否适合校园 ACG 频道公开分享（色情、血腥、政治、真实人物丑化 = false）。\n"
        "what：画面一句描述，20 字内。\n"
        "character：角色名，没有就空字符串。\n"
        "quote：这个角色可能会说的一句短台词，不确定就空字符串。\n"
        "roast：一句吐槽，25 字内，可以毒舌但不要人身攻击。"
    )
    body = {
        "model": luna_chat.VISION_MODEL,
        "messages": [
            {"role": "system", "content": luna_chat.persona() + STYLE_NOTE},
            {"role": "user", "content": [{"type": "text", "text": prompt}, part]},
        ],
        "temperature": 0.8,
        "max_tokens": 320,
    }
    try:
        response = requests.post(
            luna_chat.API_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body,
            timeout=40,
        )
        response.raise_for_status()
        payload = response.json()
        token_usage.from_response("channel", luna_chat.VISION_MODEL, payload, images=1)
        text = str(payload["choices"][0]["message"]["content"] or "").strip()
        matched = re.search(r"\{.*\}", text, re.S)
        if not matched:
            return {}
        data = json.loads(matched.group(0))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log(f"describe failed: {exc}")
        return {}


def text_reply(prompt: str) -> str:
    key = luna_chat.api_key()
    if not key:
        return ""
    body = {
        "model": luna_chat.MODEL,
        "messages": [
            {"role": "system", "content": luna_chat.persona() + STYLE_NOTE},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.9,
        "max_tokens": 160,
    }
    try:
        response = requests.post(
            luna_chat.API_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        token_usage.from_response("channel", luna_chat.MODEL, payload)
        return str(payload["choices"][0]["message"]["content"] or "").strip()
    except Exception as exc:
        log(f"text failed: {exc}")
        return ""


def clean(text: str, limit: int) -> str:
    value = re.sub(r"[「」“”\"']", "", str(text or ""))
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def compose(slot: str, info: dict, author: str) -> tuple[str, str]:
    greeting = SLOTS[slot]["greeting"]
    what = clean(info.get("what"), 24)
    quote = clean(info.get("quote"), 24)
    roast = clean(info.get("roast"), 26)
    fallback = clean(info.get("character"), 12)

    if SLOTS[slot]["kind"] == "meme":
        title = f"{greeting}，{roast or '今日电子榨菜，凑合看。'}"
        return clean(title, 40), "中午的电子榨菜。"

    styles = [roast, quote, what]
    style = random.choice([item for item in styles if item] or [fallback or "随便看看"])
    if style == quote and quote:
        title = f"{greeting}。「{quote}」"
        body = what or roast or "看图。"
    elif style == roast and roast:
        title = f"{greeting}，{roast}"
        body = what or "看图。"
    else:
        title = f"{greeting}，{what or '一张图。'}"
        body = roast if roast and roast != what else "看图。"
    if author:
        body += f"\n画师：{author}"
    else:
        body += "\n画师：不详"
    return clean(title, 40), body[:120]


def publish_image(path: Path) -> str:
    normalized = normalize_image(path)
    url = qq_forum.image_url(normalized)
    log(f"uploaded {normalized.name} -> {url}")
    return url


def post_slot(slot: str, force: bool = False, dry_run: bool = False) -> dict:
    state = load_state()
    key = slot_key(slot)
    if not force and state.get("posts", {}).get(key):
        return {"slot": slot, "status": "done"}
    if not force and time.time() - float((state.get("fails") or {}).get(key) or 0) < 300:
        return {"slot": slot, "status": "cooldown"}
    if not force and not slot_due(slot):
        return {"slot": slot, "status": "not_due"}

    channels = config_channels()
    channel_id = channels["image" if SLOTS[slot]["kind"] == "image" else "meme"]
    if not channel_id:
        return {"slot": slot, "status": "no_channel"}

    kind = SLOTS[slot]["kind"]
    seen = state.setdefault("seen", [])
    picked = None
    info = {}
    reason = "no_image"
    for _ in range(3):
        folder_file = None
        try:
            if kind == "meme":
                folder_file = pick_meme_file()
                if folder_file:
                    picked = {
                        "path": folder_file,
                        "author": "",
                        "source": f"folder:{folder_file.name}",
                        "folder": folder_file,
                    }
                    log(f"meme from folder: {folder_file.name}")
                else:
                    picked = fetch_meme()
            else:
                picked = fetch_acg(seen)
        except Exception as exc:
            reason = str(exc)
            log(f"pick failed: {exc}")
            continue

        sha = image_bytes_sha(picked["path"])
        picked["sha"] = sha
        if not force and sha in seen:
            reason = "duplicate"
            picked = None
            continue

        info = describe(picked["path"], kind)
        if not info:
            reason = "no_caption"
            picked = None
            continue
        if info.get("safe") is False:
            reason = "unsafe"
            picked = None
            continue
        break

    if not picked:
        log(f"no usable image for {slot}: {reason}")
        return {"slot": slot, "status": reason}
    sha = picked["sha"]
    source_file = picked.get("folder")

    title, body = compose(slot, info, picked.get("author", ""))
    if dry_run:
        return {
            "slot": slot,
            "status": "dry",
            "title": title,
            "body": body,
            "image": str(picked["path"]),
            "source": picked["source"],
        }

    url = publish_image(picked["path"])
    paragraphs = [qq_forum.text_paragraph(body), qq_forum.image_paragraph(url)]
    qq_forum.post_thread(channel_id, title, paragraphs)

    thread_id = ""
    for _ in range(3):
        time.sleep(8)
        try:
            for thread in qq_forum.list_threads(channel_id):
                info_thread = thread.get("thread_info") or {}
                if info_thread.get("title") == title:
                    thread_id = str(info_thread.get("thread_id") or "")
                    break
        except Exception as exc:
            log(f"verify failed: {exc}")
        if thread_id:
            break

    if not thread_id:
        log(f"posted but not visible yet: {title}")
        return {"slot": slot, "status": "unverified", "title": title, "image": picked["source"]}

    state.setdefault("posts", {})[key] = {
        "at": int(time.time()),
        "channel": channel_id,
        "title": title,
        "thread": thread_id,
    }
    state["seen"] = (seen + [sha])[-MAX_SEEN:]
    save_state(state)

    if source_file and source_file.exists():
        try:
            USED_DIR.mkdir(parents=True, exist_ok=True)
            source_file.rename(USED_DIR / f"{int(time.time())}-{source_file.name}")
        except Exception as exc:
            log(f"move used failed: {exc}")

    log(f"posted {key} thread={thread_id} title={title}")
    return {"slot": slot, "status": "posted", "title": title, "thread": thread_id}


def comment_once(force: bool = False, dry_run: bool = False) -> dict:
    state = load_state()
    now = datetime.now()
    if not force and not comment_due(now):
        return {"status": "not_due"}
    if not force and state.get("commented", {}).get(f"{now:%Y-%m-%d}"):
        return {"status": "done"}
    if not force and float(state.get("commentBlockedUntil") or 0) > time.time():
        return {"status": "blocked"}

    try:
        bot_id = str(qq_forum.me().get("id") or "")
    except Exception as exc:
        log(f"me failed: {exc}")
        return {"status": "error", "message": str(exc)}

    channels = config_channels()
    cutoff = time.time() - 14 * 86400
    skip_words = ("露娜", "升格网络", "bot", "机器人", "公告", "测试")
    candidates = []
    for channel_id in channels["comment"] or [channels["image"], channels["meme"]]:
        if not channel_id:
            continue
        try:
            threads = qq_forum.list_threads(channel_id)
        except Exception as exc:
            log(f"threads failed {channel_id}: {exc}")
            continue
        for thread in threads:
            info = thread.get("thread_info") or {}
            thread_id = str(info.get("thread_id") or "")
            author = str(thread.get("author_id") or "")
            date = str(info.get("date_time") or "")
            if not thread_id or not info.get("title"):
                continue
            if any(word in str(info.get("title")).lower() for word in skip_words):
                continue
            if author == bot_id:
                continue
            if thread_id in (state.get("commentedThreads") or {}):
                continue
            try:
                stamp = datetime.fromisoformat(date.replace("Z", "+00:00")).timestamp()
            except Exception:
                stamp = 0
            if stamp and stamp < cutoff:
                continue
            candidates.append(
                {
                    "channel": channel_id,
                    "thread": thread_id,
                    "author": author,
                    "title": str(info.get("title")),
                    "date": date,
                }
            )

    if not candidates:
        return {"status": "no_candidate"}

    picked = random.choice(candidates)
    prompt = (
        f"频道里有人发了帖子，标题是「{picked['title']}」。"
        "用露娜的口吻评论一句，20 字内，冷淡、简短、不像客服。"
        "像熟人随口接一句，不要泼冷水，不要否定对方，最多轻微吐槽。只输出这一句。"
    )
    reply = clean(text_reply(prompt), 40)
    if not reply:
        reply = "……看到了。"

    if dry_run:
        return {"status": "dry", "target": picked, "reply": reply}

    try:
        qq_forum.comment_thread(picked["channel"], picked["thread"], picked["author"], reply, picked["date"])
    except qq_forum.ForumError as exc:
        if exc.code == 11253:
            state["commentBlockedUntil"] = int(time.time()) + COMMENT_BLOCK_SECONDS
            save_state(state)
            log("comment blocked by whitelist (11253), retry in 7 days")
            return {"status": "blocked", "message": str(exc)}
        log(f"comment failed: {exc} ({exc.code})")
        return {"status": "error", "message": str(exc), "code": exc.code}

    state.setdefault("commented", {})[f"{now:%Y-%m-%d}"] = int(time.time())
    state.setdefault("commentedThreads", {})[picked["thread"]] = int(time.time())
    save_state(state)
    log(f"commented thread={picked['thread']} reply={reply}")
    return {"status": "commented", "reply": reply, "thread": picked["thread"]}


def run_once() -> list[dict]:
    results = []
    now = datetime.now()
    for slot in SLOTS:
        if is_weekend(now) and SLOTS[slot]["start"] <= minutes_of(now) < SLOTS[slot]["end"]:
            try:
                result = post_slot(slot)
                results.append(result)
                if result.get("status") not in ("posted", "done"):
                    latest = load_state()
                    latest.setdefault("fails", {})[slot_key(slot)] = int(time.time())
                    save_state(latest)
            except Exception as exc:
                log(f"slot {slot} failed: {exc}")
                results.append({"slot": slot, "status": "error", "message": str(exc)})
                latest = load_state()
                latest.setdefault("fails", {})[slot_key(slot)] = int(time.time())
                save_state(latest)
    if comment_due(now):
        try:
            results.append(comment_once())
        except Exception as exc:
            log(f"comment failed: {exc}")
            results.append({"status": "error", "message": str(exc)})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="频道发帖 / 评论")
    parser.add_argument("--dry-run", action="store_true", help="只选图和生成配文，不发布")
    parser.add_argument("--slot", choices=list(SLOTS), default=None, help="强制发某个时段")
    parser.add_argument("--comment", action="store_true", help="强制评论一条")
    parser.add_argument("--status", action="store_true", help="查看状态")
    args = parser.parse_args()

    if args.status:
        state = load_state()
        print(json.dumps({k: v for k, v in state.items() if k != "seen"}, ensure_ascii=False, indent=1)[:1500])
        print("seen:", len(state.get("seen") or []))
        print("channels:", config_channels())
        return 0

    if args.comment:
        print(json.dumps(comment_once(force=True, dry_run=args.dry_run), ensure_ascii=False))
        return 0

    slots = [args.slot] if args.slot else list(SLOTS)
    for slot in slots:
        try:
            print(json.dumps(post_slot(slot, force=bool(args.slot), dry_run=args.dry_run), ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({"slot": slot, "status": "error", "message": str(exc)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
