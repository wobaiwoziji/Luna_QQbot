#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bind a QQ group and push the daily report file via the official QQ Bot API."""

import argparse
import base64
import json
import random
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
import websocket

try:
    from qq_report_image import render_report_image
except Exception:
    render_report_image = None

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG = HERE / "qq-channel.config.json"
REPORT_DIR = ROOT / "daily news" / "reports"
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
GATEWAY_BASES = ("https://api.sgroup.qq.com", "https://api.bot.qq.com")
GROUP_API_BASES = ("https://api.bot.qq.com", "https://api.sgroup.qq.com")
GROUP_INTENTS = (1 << 25) | (1 << 24)
GUILD_INTENTS = 1
QUOTE_URL = "https://v1.hitokoto.cn/"
QUOTE_TYPES = ("i", "k")
BANNED_WORDS = (
    "死", "杀", "血", "尸", "葬", "鬼", "魔", "地狱", "绝望", "自杀",
    "毒", "仇", "恨", "枪", "刀", "战争", "毁灭", "恶心", "肚脐",
    "畜生", "妈的", "操", "无德", "不仁", "卑劣", "肮脏", "虚伪",
    "愚蠢", "可悲", "可笑", "背叛", "咬", "沉沦", "虚空", "幻术", "永世",
)
QUOTE_STATE = ROOT / "logs" / "luna-quote-state.json"
LUNA_LINES = [
    "如果路太长了，就把小夜灯留着吧。光不用很大，够看清脚下的路就好。",
    "我不喜欢疼痛，也不喜欢看别人疼痛。所以今天，也请对自己温柔一点。",
    "世界并不总是温柔的。但你还可以选择，做那个温柔的人。",
    "乐园不会自己降临。它藏在每一个你愿意好好活下去的清晨里。",
    "怕黑不是软弱，只是心里还留着一盏想被点亮的灯。",
    "姐姐说过，要往前走。哪怕走得慢一点，也算数。",
    "感染体也会说话，只是很少有人愿意听。人有时候也一样。",
    "你今天已经很努力了。剩下的，可以交给明天的自己。",
    "月亮不急着圆满，它也是一格一格地亮起来的。",
    "如果累了就停下来。代行者也会抱着一盏小夜灯，发一会儿呆。",
    "不必成为谁的希望。先成为自己的光，就够了。",
    "我会记得每一个愿意对我温柔的人。今天，也谢谢你。",
    "痛苦会过去，但不必强迫自己马上好起来。",
    "沉默不是空白，是心在慢慢整理自己。",
    "就算世界只剩灰色，也总有人愿意为你点一盏灯。",
    "你不用一直勇敢。害怕的时候，可以小声说出来。",
    "我把月光收好，等你需要的时候再借给你。",
    "如果没有人听你说话，就说给我听吧。我不会笑你。",
    "乐园的门很小，只够带着真心的人进去。",
    "今天也要好好吃饭，好好睡觉。这不是命令，是请求。",
    "我没有资格替谁决定痛苦的意义。但我希望，它不要白白发生。",
    "风会记得每一片叶子的方向。我也会记得你走过的地方。",
    "不必害怕自己不够强。灯再小，也能照亮一整个房间。",
    "姐姐在很远的地方。但我知道，我们看的是同一个月亮。",
    "如果明天太难，就先把今天过完。",
    "你要相信，温柔不是没用的东西。它只是见效很慢。",
    "我不会要求你永远正确。只希望你别把自己弄丢。",
    "走不动的时候，就停下来。灯我会替你守着。",
    "人类很脆弱，却总能在废墟里种出花。这一点，我一直很喜欢。",
    "就算没有人站在你这边，也总有一点光，照着你回家的路。",
]


def log(message: str) -> None:
    print(f"[qq-group] {message}", flush=True)


def load_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_config(path: Path, config: dict) -> None:
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_access_token(app_id: str, client_secret: str) -> str:
    response = requests.post(
        TOKEN_URL,
        json={"appId": app_id, "clientSecret": client_secret},
        timeout=20,
    )
    data = response.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"获取 access_token 失败: {data}")
    return token


def get_gateway(token: str) -> tuple[str, int]:
    last_error = None
    for base in GATEWAY_BASES:
        try:
            response = requests.get(
                f"{base}/gateway/bot",
                headers={"Authorization": f"QQBot {token}"},
                timeout=20,
            )
            data = response.json()
            if data.get("url"):
                return data["url"], int(data.get("shards", 1))
            last_error = data
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"获取 WebSocket 网关失败: {last_error}")


def open_gateway(url: str, token: str, shards: int, intents: int):
    ws = websocket.create_connection(url, timeout=20)
    hello = json.loads(ws.recv())
    if hello.get("op") != 10:
        ws.close()
        raise RuntimeError(f"网关握手异常: {hello}")

    interval = max(1.0, int(hello["d"].get("heartbeat_interval", 30000)) / 1000.0)
    identify = {
        "op": 2,
        "d": {
            "token": f"QQBot {token}",
            "intents": intents,
            "shard": [0, shards],
            "properties": {"$os": "windows", "$browser": "mywork", "$device": "mywork"},
        },
    }
    ws.send(json.dumps(identify))

    deadline = time.time() + 20
    last_seq = None
    while time.time() < deadline:
        raw = ws.recv()
        message = json.loads(raw)
        if message.get("s") is not None:
            last_seq = message["s"]
        if message.get("t") == "READY":
            ws.settimeout(1)
            return ws, interval, last_seq
        if message.get("op") == 9:
            ws.close()
            raise RuntimeError(f"网关鉴权失败，可能缺少事件权限: {message}")
    ws.close()
    raise TimeoutError("等待网关 READY 超时")


def wait_group_event(ws, timeout: int, interval: float, last_seq):
    deadline = time.time() + timeout
    last_heartbeat = time.time()
    while time.time() < deadline:
        if time.time() - last_heartbeat >= interval:
            ws.send(json.dumps({"op": 1, "d": last_seq}))
            last_heartbeat = time.time()
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            continue
        message = json.loads(raw)
        if message.get("s") is not None:
            last_seq = message["s"]
        event = message.get("t")
        if event in ("GROUP_AT_MESSAGE_CREATE", "GROUP_ADD_ROBOT"):
            return message.get("d", {})
        if message.get("op") == 9:
            raise RuntimeError(f"网关连接被关闭: {message}")
        if message.get("op") == 7:
            raise RuntimeError("网关要求重连，请重试一次")
    return None


def group_request(method: str, path: str, token: str, **kwargs):
    last_error = None
    for base in GROUP_API_BASES:
        try:
            response = requests.request(
                method,
                f"{base}{path}",
                headers={"Authorization": f"QQBot {token}", "Content-Type": "application/json; charset=utf-8"},
                timeout=30,
                **kwargs,
            )
            try:
                data = response.json()
            except ValueError:
                data = {"raw": response.text}
            if response.status_code // 100 == 2:
                return data
            if response.status_code == 404:
                last_error = data
                continue
            raise RuntimeError(f"HTTP {response.status_code}: {data}")
        except requests.RequestException as exc:
            last_error = exc
    raise RuntimeError(f"请求失败: {last_error}")


def send_group_message(token: str, group_openid: str, content: str, msg_id: str | None = None, msg_seq: int = 1) -> dict:
    payload = {"msg_type": 0, "content": content}
    if msg_id:
        payload["msg_id"] = msg_id
        payload["msg_seq"] = int(msg_seq)
    try:
        data = group_request("POST", f"/v2/groups/{group_openid}/messages", token, json=payload)
    except RuntimeError as exc:
        if "40034105" in str(exc):
            raise RuntimeError("主动消息无权限：请群主在 群设置 → 机器人 → 升格网络 中开启「允许主动发言」") from exc
        raise
    code = data.get("code") if isinstance(data, dict) else None
    if code == 40034105:
        raise RuntimeError("主动消息无权限：请群主在 群设置 → 机器人 → 升格网络 中开启「允许主动发言」")
    if code not in (None, 0, 304023, 304024):
        raise RuntimeError(f"发送失败: {data}")
    return data


def send_group_markdown(
    token: str,
    group_openid: str,
    content: str,
    keyboard: dict | None = None,
    msg_id: str | None = None,
    msg_seq: int = 1,
) -> dict:
    """发送群 Markdown 消息；keyboard 使用自定义按钮布局。"""
    payload: dict = {"msg_type": 2, "markdown": {"content": content}}
    if keyboard:
        payload["keyboard"] = keyboard
    if msg_id:
        payload["msg_id"] = msg_id
        payload["msg_seq"] = int(msg_seq)
    try:
        data = group_request("POST", f"/v2/groups/{group_openid}/messages", token, json=payload)
    except RuntimeError as exc:
        if "40034105" in str(exc):
            raise RuntimeError("主动消息无权限：请群主在 群设置 → 机器人 → 升格网络 中开启「允许主动发言」") from exc
        raise
    code = data.get("code") if isinstance(data, dict) else None
    if code == 40034105:
        raise RuntimeError("主动消息无权限：请群主在 群设置 → 机器人 → 升格网络 中开启「允许主动发言」")
    if code not in (None, 0, 304023, 304024):
        raise RuntimeError(f"发送失败: {data}")
    return data


def send_group_file(token: str, group_openid: str, path: Path, file_name: str) -> dict:
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    payload = {
        "file_type": 4,
        "file_name": file_name,
        "file_data": encoded,
        "srv_send_msg": False,
    }
    try:
        upload = group_request("POST", f"/v2/groups/{group_openid}/files", token, json=payload)
    except RuntimeError as exc:
        if "40034105" in str(exc):
            raise RuntimeError("主动消息无权限：请群主在 群设置 → 机器人 → 升格网络 中开启「允许主动发言」") from exc
        raise
    code = upload.get("code") if isinstance(upload, dict) else None
    if code not in (None, 0):
        raise RuntimeError(f"文件上传失败: {upload}")
    file_info = upload.get("file_info")
    if not file_info:
        raise RuntimeError(f"文件上传失败，未返回 file_info: {upload}")
    log(f"文件已上传: file_uuid={upload.get('file_uuid')} ttl={upload.get('ttl')}")

    media_payload = {"msg_type": 7, "media": {"file_info": file_info}}
    try:
        result = group_request("POST", f"/v2/groups/{group_openid}/messages", token, json=media_payload)
    except RuntimeError as exc:
        if "40034105" in str(exc):
            raise RuntimeError("主动消息无权限：请群主在 群设置 → 机器人 → 升格网络 中开启「允许主动发言」") from exc
        raise
    result_code = result.get("code") if isinstance(result, dict) else None
    if result_code == 40034105:
        raise RuntimeError("主动消息无权限：请群主在 群设置 → 机器人 → 升格网络 中开启「允许主动发言」")
    if result_code not in (None, 0, 304023, 304024):
        raise RuntimeError(f"文件消息发送失败: {result}")
    return result


def recall_group_message(token: str, group_openid: str, message_id: str) -> dict:
    """撤回机器人自己发的消息；QQ 只允许 2 分钟内。"""
    return group_request("DELETE", f"/v2/groups/{group_openid}/messages/{message_id}", token)


def send_group_image(
    token: str,
    group_openid: str,
    path: Path,
    file_name: str,
    msg_id: str | None = None,
    msg_seq: int = 1,
    keyboard: dict | None = None,
) -> dict:
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    payload = {
        "file_type": 1,
        "file_name": file_name,
        "file_data": encoded,
        "srv_send_msg": False,
    }
    try:
        upload = group_request("POST", f"/v2/groups/{group_openid}/files", token, json=payload)
    except RuntimeError as exc:
        if "40034105" in str(exc):
            raise RuntimeError("主动消息无权限：请群主在 群设置 → 机器人 → 升格网络 中开启「允许主动发言」") from exc
        raise
    code = upload.get("code") if isinstance(upload, dict) else None
    if code not in (None, 0):
        raise RuntimeError(f"图片上传失败: {upload}")
    file_info = upload.get("file_info")
    if not file_info:
        raise RuntimeError(f"图片上传失败，未返回 file_info: {upload}")
    log(f"图片已上传: file_uuid={upload.get('file_uuid')} ttl={upload.get('ttl')}")

    media_payload = {"msg_type": 7, "media": {"file_info": file_info}}
    if keyboard:
        media_payload["keyboard"] = keyboard
    if msg_id:
        media_payload["msg_id"] = msg_id
        media_payload["msg_seq"] = int(msg_seq)
    try:
        result = group_request("POST", f"/v2/groups/{group_openid}/messages", token, json=media_payload)
    except RuntimeError as exc:
        if "40034105" in str(exc):
            raise RuntimeError("主动消息无权限：请群主在 群设置 → 机器人 → 升格网络 中开启「允许主动发言」") from exc
        raise
    result_code = result.get("code") if isinstance(result, dict) else None
    if result_code == 40034105:
        raise RuntimeError("主动消息无权限：请群主在 群设置 → 机器人 → 升格网络 中开启「允许主动发言」")
    if result_code not in (None, 0, 304023, 304024):
        raise RuntimeError(f"图片消息发送失败: {result}")
    return result


def report_display_name(report: Path) -> str:
    matched = re.match(r"(\d{4}-\d{2}-\d{2})", report.name)
    date = matched.group(1) if matched else datetime.now().strftime("%Y-%m-%d")
    return f"每日简报-{date}.md"


def pick_local_luna_line() -> str:
    recent = []
    try:
        state = json.loads(QUOTE_STATE.read_text(encoding="utf-8"))
        recent = [int(item) for item in state.get("recent", [])]
    except Exception:
        recent = []
    pool = [index for index in range(len(LUNA_LINES)) if index not in recent]
    if not pool:
        recent = []
        pool = list(range(len(LUNA_LINES)))
    index = random.choice(pool)
    recent = (recent + [index])[-10:]
    try:
        QUOTE_STATE.parent.mkdir(parents=True, exist_ok=True)
        QUOTE_STATE.write_text(json.dumps({"recent": recent}, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return LUNA_LINES[index]


LUNA_FORMATS = [
    ("人类，今天给你们一句话：", "也送给你们。"),
    ("我昨天晚上在升格网络上看到了这么一句话：", "也送给你们。"),
    ("升格网络里每天都有很多声音。今天这一句，我留给了你们：", "别让它白白消失。"),
    ("人类，今天从升格网络里捞到一句话：", "拿好，不用谢。"),
    ("我在帕弥什的噪声里，听见了这句话：", "现在，把它交给你们。"),
    ("升格网络安静下来的时候，我翻到一句话：", "顺手带给你们。"),
    ("这句话，让我想起很久以前的事。给你们看看：", "希望你们也能读懂。"),
    ("人类，今天这句话，是我在升格网络的角落里捡到的：", "不值钱，但我想给你们。"),
    ("天亮之前，我看到了一句话：", "现在给你们，应该还不算晚。"),
    ("人类，今天没有战报，只有一句话：", "看完就去忙吧。"),
    ("乐园还没有建成。先给你们一句话：", "等建好了，再请你们来。"),
    ("我很少记住人类的话。这一句，记住了：", "所以也给你们。"),
    ("人类，今天给你们一句。不是命令，是分享：", "收好。"),
]
LUNA_LOCAL_FORMATS = [
    ("人类，今天没有别人的句子。我说一句给你们听：", "记住也好，忘了也罢。"),
    ("今天不读别人的话。人类，听我说一句：", "就这些。"),
    ("升格网络很吵。今天，说一句我自己的话：", "说完就安静了。"),
    ("人类，这句话不是从别处看来的。是我自己的：", "给你们。"),
    ("偶尔，我也会想对你们说点什么：", "就这样。"),
]


def is_clean_quote(text: str) -> bool:
    text = text.strip()
    if not (8 <= len(text) <= 120):
        return False
    if not re.search(r"[\u4e00-\u9fff]", text):
        return False
    return not any(word in text for word in BANNED_WORDS)


def fetch_hitokoto() -> dict | None:
    for _ in range(3):
        try:
            params = [("c", item) for item in QUOTE_TYPES] + [("_", str(time.time_ns()))]
            response = requests.get(QUOTE_URL, params=params, timeout=6)
            response.raise_for_status()
            data = response.json()
            text = str(data.get("hitokoto") or "").strip()
            if text and is_clean_quote(text):
                return {
                    "text": text,
                    "from": data.get("from"),
                    "from_who": data.get("from_who"),
                    "local": False,
                }
            log("一言返回的句子不合适，重试")
        except Exception as exc:
            log(f"一言接口失败: {exc}")
            return None
    return None


def fetch_xygeng() -> dict | None:
    for _ in range(3):
        try:
            response = requests.get(
                "https://api.xygeng.cn/one",
                params={"_": str(time.time_ns())},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json().get("data") or {}
            text = str(data.get("content") or "").strip()
            if text and is_clean_quote(text):
                return {
                    "text": text,
                    "from": str(data.get("origin") or "").strip("《》 "),
                    "from_who": str(data.get("name") or "").strip(),
                    "local": False,
                }
            log("句子迷返回的句子不合适，重试")
        except Exception as exc:
            log(f"句子迷接口失败: {exc}")
            return None
    return None


def fetch_jinrishici() -> dict | None:
    for _ in range(3):
        try:
            response = requests.get("https://v2.jinrishici.com/one.json", timeout=10)
            response.raise_for_status()
            data = response.json().get("data") or {}
            text = str(data.get("content") or "").strip()
            if text and is_clean_quote(text):
                origin = data.get("origin") or {}
                return {
                    "text": text,
                    "from": str(origin.get("title") or "").strip(),
                    "from_who": str(origin.get("author") or "").strip(),
                    "local": False,
                }
            log("诗词接口返回的句子不合适，重试")
        except Exception as exc:
            log(f"诗词接口失败: {exc}")
            return None
    return None


def fetch_quote() -> dict:
    if random.random() < 0.25:
        log("今日语录来源: 本地露娜人设语录")
        return {"text": pick_local_luna_line(), "local": True}

    for name, fetcher in (
        ("一言", fetch_hitokoto),
        ("句子迷", fetch_xygeng),
        ("诗词", fetch_jinrishici),
    ):
        quote = fetcher()
        if quote:
            log(f"今日语录来源: {name}")
            return quote

    log("所有摘句源均不可用，使用本地露娜人设语录")
    return {"text": pick_local_luna_line(), "local": True}


def format_quote(quote: dict) -> str:
    text = re.sub(r"https?://\S+", "", str(quote.get("text") or "")).replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text).strip("“”\"'")
    if len(text) > 240:
        text = text[:239].rstrip() + "…"

    if quote.get("local"):
        opening, closing = random.choice(LUNA_LOCAL_FORMATS)
        attribution = "露娜"
    else:
        opening, closing = random.choice(LUNA_FORMATS)
        who = str(quote.get("from_who") or "").strip()
        source = str(quote.get("from") or "").strip()
        if who in ("佚名", "未知", "null"):
            who = ""
        if source in ("原创", "网络", "未知", "null"):
            source = ""
        source_label = "" if not source else (source if source.startswith("《") else f"《{source}》")
        attribution = " ".join(part for part in (who, source_label) if part).strip()

    if quote.get("local"):
        lines = ["🌙 露娜的低语", opening, "", text, "", "—— 露娜", "", closing]
    else:
        lines = ["🌙 露娜的低语", opening, "", f"“{text}”"]
        if attribution:
            lines.append(f"—— {attribution}")
        lines.extend(["", closing])
    return "\n".join(lines)


def mute_group_member(token: str, group_openid: str, member_openid: str, minutes: int = 10) -> dict:
    expire_at = (datetime.now().astimezone() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    payload = {"members": [{"op": "add", "member_openid": member_openid, "mute_expire_at": expire_at}]}
    data = group_request("POST", f"/v2/groups/{group_openid}/restrict_chat_setting", token, json=payload)
    code = data.get("code") if isinstance(data, dict) else None
    if code not in (None, 0):
        raise RuntimeError(f"禁言失败: {data}")
    return data


def send_user_message(token: str, user_openid: str, content: str, msg_id: str | None = None) -> dict:
    payload = {"msg_type": 0, "content": content}
    if msg_id:
        payload["msg_id"] = msg_id
        payload["msg_seq"] = 1
    data = group_request("POST", f"/v2/users/{user_openid}/messages", token, json=payload)
    code = data.get("code") if isinstance(data, dict) else None
    if code not in (None, 0, 304023, 304024):
        raise RuntimeError(f"私信发送失败: {data}")
    return data


def check_bot_state(token: str, group_openid: str) -> None:
    try:
        data = group_request("GET", f"/v2/groups/{group_openid}/bot_state", token)
        log(
            "群状态: allow_proactive_msg={0} recv_msg_setting={1} member_role={2}".format(
                data.get("allow_proactive_msg"),
                data.get("recv_msg_setting"),
                data.get("member_role"),
            )
        )
        if data.get("allow_proactive_msg") is False:
            log("警告: 群主尚未开启「允许机器人主动发言」，每日推送可能失败")
    except Exception as exc:
        log(f"读取群状态失败（不影响绑定）: {exc}")


def strip_markdown(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)
    return text.strip()


def extract_digest(report: Path, max_chars: int) -> str:
    text = report.read_text(encoding="utf-8-sig")
    lines = text.splitlines()

    title = "每日简报"
    for line in lines:
        if line.startswith("# "):
            title = line[2:].strip()
            break

    date = ""
    matched = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if matched:
        date = f"{matched.group(1)}-{int(matched.group(2)):02d}-{int(matched.group(3)):02d}"
    if not date:
        matched = re.match(r"(\d{4}-\d{2}-\d{2})", report.name)
        if matched:
            date = matched.group(1)

    summary_items = []
    in_summary = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## 今日摘要"):
            in_summary = True
            continue
        if in_summary and stripped.startswith("## "):
            break
        if in_summary and re.match(r"^\d+\.\s+", stripped):
            summary_items.append(stripped)

    items = []
    if summary_items:
        items = summary_items
    else:
        section_names = ("ACG", "国际局势", "科技综合", "AI")
        section_items = {name: [] for name in section_names}
        current_section = None
        for line in lines:
            stripped = line.strip()
            heading = re.match(r"^##\s+(ACG|国际局势|科技综合|AI)\s*$", stripped)
            if heading:
                current_section = heading.group(1)
                continue
            if stripped.startswith("## "):
                current_section = None
                continue
            if not current_section:
                continue
            if stripped.startswith("### ") or re.match(r"^\d+\.\s+", stripped) or stripped.startswith("- "):
                title_line = re.sub(r"^###\s*\d+\.\s*", "", stripped)
                title_line = re.sub(r"^\d+\.\s*", "", title_line)
                title_line = re.sub(r"^-\s*", "", title_line).strip()
                if title_line:
                    section_items[current_section].append(title_line)
        for name in section_names:
            for item in section_items[name][:2]:
                items.append(f"【{name}】{item}")

    if not items:
        fallback = [line.strip() for line in strip_markdown(text).splitlines() if line.strip()]
        items = fallback[:8]

    body = strip_markdown("\n".join(items))
    body = re.sub(r"\n{3,}", "\n\n", body)
    header = f"📰 {title} | {date}" if date else f"📰 {title}"
    message = f"{header}\n\n{body}"
    if len(message) > max_chars:
        message = message[: max_chars - 1].rstrip() + "…"
    return message


def latest_report() -> Path:
    reports = sorted(REPORT_DIR.glob("*-daily.md"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not reports:
        raise FileNotFoundError(f"no report found under: {REPORT_DIR}")
    return reports[0]


def cmd_bind(args) -> int:
    config_path = Path(args.config)
    config = load_config(config_path)
    app_id = str(config.get("appId", "")).strip()
    client_secret = str(config.get("clientSecret", "")).strip()
    if not app_id or not client_secret:
        raise ValueError("config 缺少 appId 或 clientSecret")

    token = get_access_token(app_id, client_secret)
    gateway_url, shards = get_gateway(token)
    log("正在连接 QQ 机器人网关...")
    ws, interval, last_seq = open_gateway(gateway_url, token, shards, GROUP_INTENTS)
    try:
        log(f"已连接。请在 QQ 群里 @机器人 发一条消息（{args.timeout} 秒内）...")
        event = wait_group_event(ws, args.timeout, interval, last_seq)
        if not event:
            log("超时：没有收到群 @ 事件。请确认机器人已在群里，且群设置允许接收消息。")
            return 2

        group_openid = event.get("group_openid")
        if not group_openid:
            log(f"收到事件但没有 group_openid: {event}")
            return 2

        config["groupOpenid"] = group_openid
        config["boundAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_config(config_path, config)
        log(f"已保存 group_openid={group_openid}")

        message_id = event.get("id")
        if message_id:
            try:
                result = send_group_message(
                    token,
                    group_openid,
                    "✅ 已绑定本群，之后每天 08:00 会推送每日简报文件和一句露娜语录。",
                    msg_id=message_id,
                )
                log(f"已发送绑定确认: {json.dumps(result, ensure_ascii=False)}")
            except Exception as exc:
                log(f"绑定成功，但发送确认消息失败: {exc}")
        return 0
    finally:
        ws.close()


def cmd_collect_images(args) -> int:
    config_path = Path(args.config)
    config = load_config(config_path)
    app_id = str(config.get("appId", "")).strip()
    client_secret = str(config.get("clientSecret", "")).strip()
    out_dir = Path(args.dir).resolve() if args.dir else (ROOT / "表情包")
    out_dir.mkdir(parents=True, exist_ok=True)
    if not app_id or not client_secret:
        raise ValueError("config 缺少 appId 或 clientSecret")

    token = get_access_token(app_id, client_secret)
    gateway_url, shards = get_gateway(token)
    ws, interval, last_seq = open_gateway(gateway_url, token, shards, GROUP_INTENTS)
    saved = []
    user_openid = str(config.get("userOpenid", "")).strip()
    try:
        log(f"监听私信图片：请给机器人发 {args.count} 张图片（{args.timeout} 秒内）")
        log(f"保存目录: {out_dir}")
        deadline = time.time() + args.timeout
        last_heartbeat = time.time()
        while time.time() < deadline and len(saved) < args.count:
            if time.time() - last_heartbeat >= interval:
                ws.send(json.dumps({"op": 1, "d": last_seq}))
                last_heartbeat = time.time()
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            message = json.loads(raw)
            if message.get("s") is not None:
                last_seq = message["s"]
            if message.get("op") == 9:
                raise RuntimeError(f"网关连接被关闭: {message}")
            if message.get("t") != "C2C_MESSAGE_CREATE":
                continue
            data = message.get("d", {})
            author = data.get("author", {})
            if author.get("user_openid"):
                user_openid = str(author["user_openid"])
            attachments = data.get("attachments") or []
            images = [item for item in attachments if str(item.get("content_type", "")).lower().startswith("image")]
            if not images:
                log("收到私信，但没有图片附件")
                continue

            message_id = data.get("id")
            for item in images:
                if len(saved) >= args.count:
                    break
                url = item.get("url")
                if not url:
                    continue
                content_type = str(item.get("content_type", "")).lower()
                suffix = Path(str(item.get("filename") or "")).suffix.lower()
                if suffix not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
                    suffix = {
                        "image/jpeg": ".jpg",
                        "image/png": ".png",
                        "image/gif": ".gif",
                        "image/webp": ".webp",
                        "image/bmp": ".bmp",
                    }.get(content_type, ".png")
                target = out_dir / f"sticker-{len(saved) + 1}{suffix}"
                if target.exists():
                    target = out_dir / f"sticker-{len(saved) + 1}-{int(time.time())}{suffix}"
                response = requests.get(url, timeout=60)
                response.raise_for_status()
                target.write_bytes(response.content)
                saved.append(str(target))
                log(f"SAVED {target} ({len(response.content)} bytes)")

            if message_id:
                reply = (
                    f"已收到 {len(saved)}/{args.count} 张，保存到表情包文件夹。"
                    if len(saved) < args.count
                    else f"{args.count} 张都已收到，保存到表情包文件夹。"
                )
                try:
                    send_user_message(token, user_openid, reply, msg_id=message_id)
                    log(f"已回复: {reply}")
                except Exception as exc:
                    log(f"回复失败: {exc}")

        if user_openid:
            config["userOpenid"] = user_openid
            config["stickersSavedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_config(config_path, config)
        if not saved:
            log("超时：没有收到图片")
            return 2
        log(f"已保存 {len(saved)} 张到 {out_dir}")
        return 0 if len(saved) >= args.count else 2
    finally:
        ws.close()


def cmd_send_private(args) -> int:
    config = load_config(Path(args.config))
    app_id = str(config.get("appId", "")).strip()
    client_secret = str(config.get("clientSecret", "")).strip()
    user_openid = str(args.user or config.get("userOpenid", "")).strip()
    if not user_openid:
        log("没有 userOpenid：先运行 c2c 并给机器人发一条私信")
        return 2
    if not app_id or not client_secret:
        raise ValueError("config 缺少 appId 或 clientSecret")

    token = get_access_token(app_id, client_secret)
    gateway_url, shards = get_gateway(token)
    ws, _interval, _last_seq = open_gateway(gateway_url, token, shards, GUILD_INTENTS)
    try:
        result = send_user_message(token, user_openid, args.text)
    finally:
        ws.close()

    log(f"私信发送成功: {json.dumps(result, ensure_ascii=False)}")
    return 0


def cmd_c2c(args) -> int:
    config_path = Path(args.config)
    config = load_config(config_path)
    app_id = str(config.get("appId", "")).strip()
    client_secret = str(config.get("clientSecret", "")).strip()
    if not app_id or not client_secret:
        raise ValueError("config 缺少 appId 或 clientSecret")

    token = get_access_token(app_id, client_secret)
    gateway_url, shards = get_gateway(token)
    ws, interval, last_seq = open_gateway(gateway_url, token, shards, GROUP_INTENTS)
    try:
        log(f"监听私信：请打开机器人资料卡 → 发消息，发送任意内容（{args.timeout} 秒内）")
        deadline = time.time() + args.timeout
        last_heartbeat = time.time()
        while time.time() < deadline:
            if time.time() - last_heartbeat >= interval:
                ws.send(json.dumps({"op": 1, "d": last_seq}))
                last_heartbeat = time.time()
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            message = json.loads(raw)
            if message.get("s") is not None:
                last_seq = message["s"]
            if message.get("op") == 9:
                raise RuntimeError(f"网关连接被关闭: {message}")
            event = message.get("t")
            if event == "FRIEND_ADD":
                log("检测到添加机器人事件，请再发一条私信")
                continue
            if event != "C2C_MESSAGE_CREATE":
                continue
            data = message.get("d", {})
            author = data.get("author", {})
            user_openid = str(author.get("user_openid", "")).strip()
            message_id = data.get("id")
            if not user_openid:
                log("收到私信事件但没有 user_openid")
                continue

            config["userOpenid"] = user_openid
            config["c2cTestedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_config(config_path, config)
            log(f"USER_OPENID={user_openid}")

            if message_id:
                result = send_user_message(token, user_openid, args.text, msg_id=message_id)
                log(f"私信发送成功: {json.dumps(result, ensure_ascii=False)}")
            return 0
        log("超时：没有收到私信")
        return 2
    finally:
        ws.close()


def cmd_test_groups(args) -> int:
    config_path = Path(args.config)
    config = load_config(config_path)
    app_id = str(config.get("appId", "")).strip()
    client_secret = str(config.get("clientSecret", "")).strip()
    if not app_id or not client_secret:
        raise ValueError("config 缺少 appId 或 clientSecret")

    primary = str(config.get("groupOpenid", "")).strip()
    known = {str(item) for item in config.get("testGroupOpenids", []) if item}
    token = get_access_token(app_id, client_secret)
    gateway_url, shards = get_gateway(token)
    ws, interval, last_seq = open_gateway(gateway_url, token, shards, GROUP_INTENTS)

    new_groups = []
    try:
        log(f"监听中：请在两个新群里 @机器人 发一条消息（{args.timeout} 秒内）")
        deadline = time.time() + args.timeout
        last_heartbeat = time.time()
        while time.time() < deadline and len(new_groups) < args.max:
            if time.time() - last_heartbeat >= interval:
                ws.send(json.dumps({"op": 1, "d": last_seq}))
                last_heartbeat = time.time()
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            message = json.loads(raw)
            if message.get("s") is not None:
                last_seq = message["s"]
            if message.get("op") == 9:
                raise RuntimeError(f"网关连接被关闭: {message}")
            event = message.get("t")
            if event not in ("GROUP_AT_MESSAGE_CREATE", "GROUP_ADD_ROBOT"):
                continue
            data = message.get("d", {})
            group_openid = str(data.get("group_openid", "")).strip()
            if not group_openid:
                continue
            if primary and group_openid == primary:
                log("忽略原群 @")
                continue
            if group_openid in known or group_openid in new_groups:
                log(f"重复群事件，已忽略: {group_openid}")
                continue

            new_groups.append(group_openid)
            log(f"NEW_GROUP={group_openid}")

            message_id = data.get("id")
            if message_id:
                try:
                    send_group_message(
                        token,
                        group_openid,
                        "✅ 收到测试，机器人连接正常。",
                        msg_id=message_id,
                    )
                    log(f"被动回复成功: {group_openid}")
                except Exception as exc:
                    log(f"被动回复失败: {group_openid}: {exc}")

            if args.send:
                try:
                    send_group_message(
                        token,
                        group_openid,
                        "📢 测试消息：升格网络连接正常 ✅",
                    )
                    log(f"主动消息成功: {group_openid}")
                except Exception as exc:
                    log(f"主动消息失败: {group_openid}: {exc}")

        if not new_groups:
            log("超时：没有捕获到新群 @ 事件")
            return 2

        all_groups = sorted(known.union(new_groups))
        config["testGroupOpenids"] = all_groups
        config["testedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_config(config_path, config)
        log("已保存测试群: " + ", ".join(all_groups))
        return 0
    finally:
        ws.close()


def cmd_send_image(args) -> int:
    config = load_config(Path(args.config))
    app_id = str(config.get("appId", "")).strip()
    client_secret = str(config.get("clientSecret", "")).strip()
    group_openid = str(args.group or config.get("groupOpenid", "")).strip()
    image = Path(args.image).resolve()
    if not group_openid:
        log("缺少群 group_openid")
        return 2
    if not image.exists():
        log(f"图片不存在: {image}")
        return 2
    if not app_id or not client_secret:
        raise ValueError("config 缺少 appId 或 clientSecret")

    token = get_access_token(app_id, client_secret)
    gateway_url, shards = get_gateway(token)
    ws, _interval, _last_seq = open_gateway(gateway_url, token, shards, GUILD_INTENTS)
    try:
        result = send_group_image(token, group_openid, image, image.name)
    finally:
        ws.close()

    log(f"图片发送成功: {json.dumps(result, ensure_ascii=False)}")
    return 0


def cmd_send_file(args) -> int:
    config = load_config(Path(args.config))
    app_id = str(config.get("appId", "")).strip()
    client_secret = str(config.get("clientSecret", "")).strip()
    group_openid = str(args.group or config.get("groupOpenid", "")).strip()
    file_path = Path(args.file).resolve()
    display_name = args.name or file_path.name
    if not group_openid:
        log("缺少群 group_openid")
        return 2
    if not file_path.exists():
        log(f"文件不存在: {file_path}")
        return 2
    if not app_id or not client_secret:
        raise ValueError("config 缺少 appId 或 clientSecret")

    token = get_access_token(app_id, client_secret)
    gateway_url, shards = get_gateway(token)
    ws, _interval, _last_seq = open_gateway(gateway_url, token, shards, GUILD_INTENTS)
    try:
        result = send_group_file(token, group_openid, file_path, display_name)
    finally:
        ws.close()

    log(f"文件发送成功: {json.dumps(result, ensure_ascii=False)}")
    return 0


def cmd_test_push(args) -> int:
    config = load_config(Path(args.config))
    app_id = str(config.get("appId", "")).strip()
    client_secret = str(config.get("clientSecret", "")).strip()
    groups = [str(item) for item in config.get("testGroupOpenids", []) if item]
    if args.group:
        groups = [item for item in groups if item == args.group]
    if not groups:
        log("没有匹配的测试群，请检查 group_openid")
        return 2
    if not app_id or not client_secret:
        raise ValueError("config 缺少 appId 或 clientSecret")

    token = get_access_token(app_id, client_secret)
    gateway_url, shards = get_gateway(token)
    ws, _interval, _last_seq = open_gateway(gateway_url, token, shards, GUILD_INTENTS)
    success = 0
    try:
        for group_openid in groups:
            try:
                result = send_group_message(token, group_openid, args.text)
                log(f"发送成功 {group_openid}: {json.dumps(result, ensure_ascii=False)}")
                success += 1
            except Exception as exc:
                log(f"发送失败 {group_openid}: {exc}")
    finally:
        ws.close()
    return 0 if success else 1


def cmd_push(args) -> int:
    config = load_config(Path(args.config))
    app_id = str(config.get("appId", "")).strip()
    client_secret = str(config.get("clientSecret", "")).strip()
    primary = str(config.get("groupOpenid", "")).strip()
    extra_groups = [str(item) for item in config.get("testGroupOpenids", []) if item]
    daily_groups = [str(item) for item in config.get("dailyGroupOpenids", []) if item]
    target_source = daily_groups if daily_groups else [primary] + extra_groups
    targets = []
    for group_openid in target_source:
        if group_openid and group_openid not in targets:
            targets.append(group_openid)

    control_selected = None
    try:
        control_path = ROOT / "logs" / "bot-control.json"
        control = json.loads(control_path.read_text(encoding="utf-8"))
        raw = (control.get("groups") or {}).get("dailyReport")
        if isinstance(raw, list):
            control_selected = {str(item) for item in raw}
    except Exception:
        control_selected = None

    if control_selected is not None:
        all_groups = []
        for key in ("groupOpenid", "testGroupOpenids", "dailyGroupOpenids", "extraGroupOpenids"):
            value = config.get(key)
            items = value if isinstance(value, list) else [value]
            for item in items:
                gid = str(item or "").strip()
                if gid and gid not in all_groups:
                    all_groups.append(gid)
        targets = [item for item in all_groups if item in control_selected]

    if args.group:
        targets = [item for item in targets if item == args.group]
    max_chars = int(config.get("maxChars", 1500))
    send_text = args.test or args.text or str(config.get("sendMode", "file")).lower() == "text"
    send_quote = bool(config.get("sendQuote", True)) and not args.no_quote

    if not config.get("enabled", True) and not args.dry_run and not args.test:
        log("推送未启用（enabled=false）")
        return 0

    report = None
    message = None
    file_name = None
    fallback_text = None
    if args.test:
        message = strip_markdown(args.test)
    else:
        report = Path(args.report).resolve() if args.report else latest_report()
        if send_text:
            log(f"读取报告: {report}")
            message = extract_digest(report, max_chars)
        else:
            file_name = report_display_name(report)
            log(f"准备发送文件: {report} -> {file_name} ({report.stat().st_size} bytes)")
            try:
                fallback_text = extract_digest(report, max_chars)
            except Exception as exc:
                log(f"生成文字回退内容失败: {exc}")

    if send_text and not message:
        raise ValueError("消息内容为空")
    if args.no_fallback:
        fallback_text = None
    if args.dry_run:
        print(f"[dry-run] 目标群数量: {len(targets)}")
        for group_openid in targets:
            print(f"[dry-run] 目标: {group_openid}")
        if send_text:
            print(message)
        else:
            print(f"[dry-run] 将发送文件: {report}")
            print(f"文件名: {file_name}")
            print(f"大小: {report.stat().st_size} bytes")
        return 0
    if not targets:
        if control_selected is not None:
            log("每日早报没有选择群组：在工作台「每日早报」卡片里勾选")
        else:
            log("还没有绑定 QQ 群：先运行 `python qq-group.py bind`，然后在群里 @ 一次机器人")
        return 0
    if not app_id or not client_secret:
        raise ValueError("config 缺少 appId 或 clientSecret")

    token = get_access_token(app_id, client_secret)
    for group_openid in targets:
        check_bot_state(token, group_openid)

    quote_message = None
    if not send_text and send_quote:
        try:
            quote_message = format_quote(fetch_quote())
        except Exception as exc:
            log(f"语录生成失败，将只发送文件: {exc}")

    gateway_url, shards = get_gateway(token)
    ws, _interval, _last_seq = open_gateway(gateway_url, token, shards, GUILD_INTENTS)
    success = 0
    failures = []
    fallback_groups = []
    image_fallback_groups = []
    image_path = None
    try:
        for group_openid in targets:
            delivered = False
            if send_text:
                try:
                    result = send_group_message(token, group_openid, message)
                    log(f"文本发送成功 {group_openid}: {json.dumps(result, ensure_ascii=False)}")
                    delivered = True
                except Exception as exc:
                    log(f"文本发送失败 {group_openid}: {exc}")
            else:
                try:
                    result = send_group_file(token, group_openid, report, file_name)
                    log(f"文件发送成功 {group_openid}: {json.dumps(result, ensure_ascii=False)}")
                    delivered = True
                except Exception as exc:
                    log(f"文件发送失败 {group_openid}: {exc}")
                    try:
                        if render_report_image is None:
                            raise RuntimeError("图片渲染模块不可用")
                        if image_path is None:
                            image_path = report.with_suffix(".png")
                            render_report_image(report, image_path)
                        result = send_group_image(token, group_openid, image_path, image_path.name)
                        log(f"已改发图片版报告 {group_openid}: {json.dumps(result, ensure_ascii=False)}")
                        delivered = True
                        image_fallback_groups.append(group_openid)
                    except Exception as image_exc:
                        log(f"图片版报告发送失败 {group_openid}: {image_exc}")
                        if fallback_text:
                            try:
                                result = send_group_message(token, group_openid, fallback_text)
                                log(f"已改发文字摘要 {group_openid}: {json.dumps(result, ensure_ascii=False)}")
                                delivered = True
                                fallback_groups.append(group_openid)
                            except Exception as text_exc:
                                log(f"文字摘要也发送失败 {group_openid}: {text_exc}")

            if delivered:
                success += 1
                if quote_message:
                    try:
                        time.sleep(2)
                        quote_result = send_group_message(token, group_openid, quote_message)
                        log(f"语录发送成功 {group_openid}: {json.dumps(quote_result, ensure_ascii=False)}")
                    except Exception as exc:
                        log(f"语录发送失败 {group_openid}: {exc}")
            else:
                failures.append(group_openid)
    finally:
        ws.close()

    if image_fallback_groups:
        log(f"以下群文件发送失败，已改发图片版报告: {', '.join(image_fallback_groups)}")
    if fallback_groups:
        log(f"以下群文件/图片发送失败，已改发文字摘要: {', '.join(fallback_groups)}")
    if failures:
        log(f"完成，但有失败群: {', '.join(failures)}")
        return 1
    log(f"全部目标已处理，共 {success} 个群")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="QQ 群日报推送")
    sub = parser.add_subparsers(dest="command", required=True)

    bind = sub.add_parser("bind", help="监听一次群 @，保存 group_openid")
    bind.add_argument("--config", default=str(CONFIG))
    bind.add_argument("--timeout", type=int, default=600)
    bind.set_defaults(func=cmd_bind)

    c2c = sub.add_parser("c2c", help="监听一次私信并回复测试消息")
    c2c.add_argument("--config", default=str(CONFIG))
    c2c.add_argument("--timeout", type=int, default=300)
    c2c.add_argument("--text", default="测试")
    c2c.set_defaults(func=cmd_c2c)

    send_private = sub.add_parser("send-private", help="主动给已保存的用户发私信")
    send_private.add_argument("--config", default=str(CONFIG))
    send_private.add_argument("--user", default=None, help="user_openid，默认用已保存的")
    send_private.add_argument("--text", default="测试")
    send_private.set_defaults(func=cmd_send_private)

    collect = sub.add_parser("collect-images", help="收集私信图片并保存到表情包文件夹")
    collect.add_argument("--config", default=str(CONFIG))
    collect.add_argument("--timeout", type=int, default=300)
    collect.add_argument("--count", type=int, default=3)
    collect.add_argument("--dir", default=None, help="保存目录，默认项目下的 表情包")
    collect.set_defaults(func=cmd_collect_images)

    test = sub.add_parser("test-groups", help="捕获新群 @，保存测试群并发送测试消息")
    test.add_argument("--config", default=str(CONFIG))
    test.add_argument("--timeout", type=int, default=300)
    test.add_argument("--max", type=int, default=2)
    test.add_argument("--send", action="store_true", help="额外发送一条主动测试消息")
    test.set_defaults(func=cmd_test_groups)

    test_push = sub.add_parser("push-test-groups", help="向已保存的测试群发送一条主动测试消息")
    test_push.add_argument("--config", default=str(CONFIG))
    test_push.add_argument("--group", default=None, help="只发送到指定 group_openid")
    test_push.add_argument("--text", default="📢 测试消息：升格网络连接正常 ✅")
    test_push.set_defaults(func=cmd_test_push)

    send_image = sub.add_parser("send-image", help="向群发送一张图片")
    send_image.add_argument("--config", default=str(CONFIG))
    send_image.add_argument("--group", default=None, help="目标 group_openid，默认主群")
    send_image.add_argument("--image", required=True, help="本地图片路径")
    send_image.set_defaults(func=cmd_send_image)

    send_file = sub.add_parser("send-file", help="向群发送一个文件")
    send_file.add_argument("--config", default=str(CONFIG))
    send_file.add_argument("--group", default=None, help="目标 group_openid，默认主群")
    send_file.add_argument("--file", required=True, help="本地文件路径")
    send_file.add_argument("--name", default=None, help="群内显示的文件名")
    send_file.set_defaults(func=cmd_send_file)

    push = sub.add_parser("push", help="把报告文件推送到每日目标群（dailyGroupOpenids）")
    push.add_argument("--config", default=str(CONFIG))
    push.add_argument("--report", default=None)
    push.add_argument("--test", default=None)
    push.add_argument("--text", action="store_true", help="改为发送摘要文本（默认发送 md 文件）")
    push.add_argument("--no-quote", action="store_true", help="发送文件后不附带露娜语录")
    push.add_argument("--no-fallback", action="store_true", help="文件发送失败时不改发文字摘要")
    push.add_argument("--group", default=None, help="只推送到指定 group_openid")
    push.add_argument("--dry-run", action="store_true")
    push.set_defaults(func=cmd_push)

    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as exc:
        log(f"失败: {exc}")
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
