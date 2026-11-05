#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QQ 频道论坛（帖子）接口：发帖、读帖、评论、图片转存。"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG_PATH = HERE / "qq-channel.config.json"
BASE = "https://api.sgroup.qq.com"


class ForumError(RuntimeError):
    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code


def log(message: str) -> None:
    print(f"[forum] {message}", flush=True)


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise ForumError(f"读取配置失败: {exc}")


_token = {"value": "", "exp": 0.0}


def access_token() -> str:
    now = time.time()
    if _token["value"] and _token["exp"] > now + 120:
        return _token["value"]
    config = load_config()
    app_id = str(config.get("appId", "")).strip()
    secret = str(config.get("clientSecret", "")).strip()
    if not app_id or not secret:
        raise ForumError("config 缺少 appId/clientSecret")
    last = None
    for _ in range(3):
        try:
            response = requests.post(
                "https://bots.qq.com/app/getAppAccessToken",
                json={"appId": app_id, "clientSecret": secret},
                timeout=25,
            )
            data = response.json()
            token = str(data.get("access_token") or "")
            if token:
                _token["value"] = token
                _token["exp"] = now + int(data.get("expires_in") or 7200)
                return token
            last = str(data)
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(2)
    raise ForumError(f"获取 access_token 失败: {last}")


def api(method: str, path: str, body: dict | None = None, timeout: int = 30) -> dict:
    headers = {"Authorization": f"QQBot {access_token()}", "Content-Type": "application/json"}
    last = None
    for attempt in range(3):
        try:
            response = requests.request(
                method, BASE + path, headers=headers, json=body, timeout=timeout
            )
            if response.status_code == 204 or not response.content:
                return {}
            try:
                data = response.json()
            except ValueError:
                return {"raw": response.text}
            if response.status_code < 300 and not data.get("code"):
                return data
            code = int(data.get("code") or 0)
            if response.status_code >= 500 and code in (0, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2)
                continue
            raise ForumError(str(data.get("message") or data), code)
        except ForumError:
            raise
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(2)
    raise ForumError(f"请求失败: {last}")


def me() -> dict:
    return api("GET", "/users/@me")


def list_channels() -> list[dict]:
    channels = []
    for guild in api("GET", "/users/@me/guilds") or []:
        data = api("GET", f"/guilds/{guild.get('id')}/channels")
        for channel in data if isinstance(data, list) else []:
            channel["guild_name"] = guild.get("name")
            channel["guild_id"] = guild.get("id")
            channels.append(channel)
    return channels


def post_thread(channel_id: str, title: str, paragraphs: list[dict]) -> dict:
    content = json.dumps({"paragraphs": paragraphs}, ensure_ascii=False)
    return api(
        "PUT",
        f"/channels/{channel_id}/threads",
        {"title": title[:80], "content": content, "format": 4},
    )


def list_threads(channel_id: str) -> list[dict]:
    data = api("GET", f"/channels/{channel_id}/threads")
    return data.get("threads") or []


def get_thread(channel_id: str, thread_id: str) -> dict:
    data = api("GET", f"/channels/{channel_id}/threads/{thread_id}")
    return data.get("thread") or {}


def delete_thread(channel_id: str, thread_id: str) -> dict:
    return api("DELETE", f"/channels/{channel_id}/threads/{thread_id}")


def comment_thread(
    channel_id: str, thread_id: str, thread_author: str, content: str, create_time: str = ""
) -> dict:
    """直接评论别人的帖子（需要白名单权限，当前应用大概率返回 11253）。"""
    body = {"thread_author": thread_author, "content": content[:400]}
    if create_time:
        body["thread_create_time"] = create_time
    return api("POST", f"/channels/{channel_id}/threads/{thread_id}/comment", body)


def reply_comment(channel_id: str, thread_id: str, comment_id: str, content: str) -> dict:
    return api(
        "POST",
        f"/channels/{channel_id}/threads/{thread_id}/comment/{comment_id}/reply",
        {"content": content[:400]},
    )


def text_paragraph(text: str) -> dict:
    return {"elems": [{"type": 1, "text": {"text": text}}], "props": {}}


def image_paragraph(url: str, width_percent: float = 1) -> dict:
    return {"elems": [{"type": 2, "image": {"third_url": url, "width_percent": width_percent}}], "props": {}}


def image_url(path: Path) -> str:
    """把本地图片传到 CloudBase 静态托管，返回可被 QQ 抓取的公网地址。"""
    config = load_config()
    env = str(config.get("cloudbaseEnv", "")).strip()
    domain = str(config.get("cloudbaseDomain", "")).strip().rstrip("/")
    if not env or not domain:
        raise ForumError("config 缺少 cloudbaseEnv/cloudbaseDomain")
    path = Path(path)
    digest = hashlib.sha1(path.read_bytes()).hexdigest()[:16]
    name = f"memes/{digest}{path.suffix.lower() or '.jpg'}"
    command = f'tcb hosting deploy "{path}" "{name}" -e {env}'
    result = subprocess.run(
        command, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180
    )
    output = f"{result.stdout or ''}{result.stderr or ''}"
    if result.returncode != 0 or "Deployment completed" not in output:
        raise ForumError(f"图片转存失败: {output.strip()[-400:]}")
    return f"{domain}/{name}"


def main() -> int:
    parser = argparse.ArgumentParser(description="QQ 频道论坛接口")
    parser.add_argument("--list", action="store_true", help="列出频道和子频道")
    parser.add_argument("--threads", default=None, help="列出某个子频道的帖子")
    parser.add_argument("--post", default=None, help="发一条纯文本测试帖到指定子频道")
    parser.add_argument("--title", default="测试帖（稍后删除）")
    parser.add_argument("--delete", nargs=2, metavar=("CHANNEL", "THREAD"), default=None)
    args = parser.parse_args()

    try:
        if args.list:
            for channel in list_channels():
                if channel.get("type") in (0, 10007):
                    print(
                        f"{channel.get('guild_name')} | {channel.get('name')} | "
                        f"type={channel.get('type')} | channel_id={channel.get('id')}"
                    )
            return 0
        if args.threads:
            for thread in list_threads(args.threads):
                info = thread.get("thread_info") or {}
                print(f"{info.get('thread_id')} | {info.get('title')} | {thread.get('author_id')}")
            return 0
        if args.post:
            result = post_thread(args.post, args.title, [text_paragraph("接口测试，稍后删除。")])
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.delete:
            print(json.dumps(delete_thread(args.delete[0], args.delete[1]), ensure_ascii=False))
            return 0
    except ForumError as exc:
        log(f"失败: {exc} (code={exc.code})")
        return 1
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
