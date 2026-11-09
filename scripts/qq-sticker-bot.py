#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Listen for @ messages: reply with a sticker or with IP/character images."""

import importlib.util
import functools
import json
import queue
import random
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import requests
import websocket

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STICKER_DIR = ROOT / "表情包"
LOG_DIR = ROOT / "logs"
LOG_PATH = LOG_DIR / "sticker-bot.log"
CONTROL_PATH = LOG_DIR / "bot-control.json"
GROUPS_PATH = LOG_DIR / "groups.json"
IMAGE_RATE_LIMIT = 10
IMAGE_COOLDOWN = 300
SUMMARY_COOLDOWN = 600
DESPISE_GAP = 3.0
DESPISE_RESET = 600
DESPISE_COOLDOWN = 600
DESPISE_LINES = [
    "惩罚在此降临。",
    "消弭吧。",
    "聆听死亡的终音。",
    "安静一会儿吧，人类。",
    "这是你自找的。",
]
DESPISE_LEVELS = ("蔑视", "王之蔑视", "神之蔑视")
DESPISE_TRIGGER = re.compile(r"(藐视|鄙视|(?:对着)?露娜(?:发电|发癫))[\s!！。.~～]*")
_despise: dict[str, dict] = {}
HINT_COOLDOWN = 60
JELLY_COOLDOWN = 60
LUNA_COOLDOWN = 20
PROGRESS_SEARCH_DELAY = 3.0
PROGRESS_DOWNLOAD_DELAY = 6.0
TASK_LIMIT = 3
TASK_WINDOW = 600
TASK_MUTE_MINUTES = 60
REPEAT_WINDOW = 300
REPEAT_LIMIT = 3
JELLY_HINT_COOLDOWN = 300
HELP_COOLDOWN = 30
MAX_IMAGES = 2
BOT_STATE_TTL = 600
_group_states: dict[str, tuple[dict, float]] = {}

HELP_URL = "https://luna-site-d0gj36nib486137a4-1524033896.tcloudbaseapp.com/index.html"
REMINDER_TOO_SHORT_LINES = (
    "……时间太短了。这种事自己记。提醒至少一分钟。",
    "十秒也要我提醒？自己盯着表去。最短一分钟。",
    "这种几秒钟的事就别麻烦我了。提醒至少一分钟。",
)


def weather_keyboard() -> dict:
    return {
        "content": {
            "rows": [
                {
                    "buttons": [
                        {
                            "id": "w1",
                            "render_data": {"label": "查天气", "visited_label": "查天气", "style": 1},
                            "action": {
                                "type": 2,
                                "permission": {"type": 2},
                                "data": "/天气 ",
                                "enter": False,
                                "unsupport_tips": "请升级 QQ",
                            },
                        }
                    ]
                }
            ]
        }
    }

HINT_TEXT = (
    "没找到「{name}」的图。角色重名时，用「角色 作品」或「作品 角色」，中间加空格：\n"
    "· 拉弥亚 战双 / 战双 拉弥亚\n"
    "· 缘结神 阴阳师 / 阴阳师 缘结神\n"
    "也可以直接给英文或日文 tag，比如 lamia_(punishing:_gray_raven)、ラミア。"
)

HELP_TEXT = (
    "🤖 升格网络 指令（@我 + /指令）\n"
    "· /找图 角色名 [1|2]：例 /找图 灵梦 2、/找图 随机图（每人 5 分钟）\n"
    "· /聊天总结：当日群聊要点卡片（每群 10 分钟，至少 5 条）\n"
    "· /露娜 想说的话：让露娜用她的口吻回你（每人 20 秒）\n"
    "· /q弹：发图片或引用图片 + /q弹 → 生成循环动图（每人 60 秒）\n"
    "· /对着露娜发电：没人知道这么做会有什么下场，没人敢尝试\n"
    "· 每日早报：每天早上自动推送早报，还有露娜清晨的第一句问候\n"
    "· /帮助：再看这份说明\n"
    "注：官方接口读不到群成员头像，/q弹 请直接把图发给我。"
)


def strip_mentions(content: str) -> str:
    text = re.sub(r"<@!?[^>]+>", " ", content or "")
    text = re.sub(r"(?:^|\s)@\S+", " ", text)
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def parse_command(content: str):
    """Only messages that start with / (or ／) are treated as commands."""
    text = strip_mentions(content)
    if not text or text[0] not in "/／":
        return None
    body = text[1:].strip()
    if not body:
        return None
    parts = body.split(None, 1)
    return parts[0], (parts[1].strip() if len(parts) > 1 else "")


def is_help(content: str) -> bool:
    return bool(re.fullmatch(r"帮助|help|菜单|使用说明", strip_mentions(content), re.I))


def extract_quote(data: dict, limit: int = 300) -> str:
    """把被引用消息里的文字抓出来（message_type=103 时 msg_elements 里带原消息）。"""
    texts: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            content = node.get("content")
            if isinstance(content, str) and content.strip():
                texts.append(content.strip())
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data.get("msg_elements"))
    current = re.sub(r"<@!?[^>]+>", "", str(data.get("content") or "")).strip()
    unique = [text for text in dict.fromkeys(texts) if text and text not in current]
    joined = " / ".join(unique).strip()
    return joined[:limit]


def punish(module, token: str, group_openid: str, user_id: str, message_id: str | None, penalties: dict, line: str, minutes: int = TASK_MUTE_MINUTES) -> None:
    """先回一句，再尝试真禁言；没权限就本地禁言。"""
    try:
        module.send_group_message(token, group_openid, line, msg_id=message_id)
    except Exception as exc:
        log(f"penalty notice failed: {exc}")
    try:
        module.mute_group_member(token, group_openid, user_id, minutes)
        log(f"penalty: muted user={user_id} {minutes}min")
        return
    except Exception as exc:
        log(f"penalty: mute failed user={user_id} ({exc}), 本地禁言兜底")
    penalties[user_id] = time.time() + minutes * 60


_reply_queues: dict[str, queue.Queue] = {}
_reply_queues_guard = threading.Lock()


def _reply_worker(group: str, work: queue.Queue) -> None:
    while True:
        label, fn, submitted = work.get()
        try:
            started = time.time()
            fn()
            waited = (started - submitted) * 1000
            spent = (time.time() - started) * 1000
            log(f"reply worker {label} group={group[:8]} wait={waited:.0f}ms run={spent:.0f}ms")
        except Exception as exc:
            log(f"reply worker failed {label} group={group[:8]}: {exc}")
        finally:
            work.task_done()


def submit_reply(group: str, label: str, fn) -> None:
    """按群排队，保证同一个群的回复顺序，不同群并行。"""
    with _reply_queues_guard:
        work = _reply_queues.get(group)
        if work is None:
            work = queue.Queue()
            _reply_queues[group] = work
            threading.Thread(
                target=_reply_worker, args=(group, work), daemon=True, name=f"reply-{group[:6]}"
            ).start()
    work.put((label, fn, time.time()))


def send_welcome(**kwargs) -> None:
    group = kwargs["group"]
    try:
        text = kwargs["welcome"].greet(kwargs["speaker"], group)
        kwargs["module"].send_group_message(
            kwargs["token"], group, text, msg_id=kwargs["message_id"], msg_seq=2
        )
        kwargs["welcome"].mark_welcome(group)
        log(f"welcome sent group={group[:8]} user={kwargs['user_id'][:8]} text={text!r}")
    except Exception as exc:
        log(f"welcome failed group={group[:8]}: {exc}")


def send_join_welcome(**kwargs) -> None:
    group = kwargs["group"]
    user = kwargs["user_id"]
    try:
        text = kwargs["welcome"].greet_join(group)
        markdown, keyboard = kwargs["welcome"].join_card(user, text)
        try:
            kwargs["module"].send_group_markdown(
                kwargs["token"], group, markdown, keyboard=keyboard
            )
        except Exception as exc:
            log(f"welcome join markdown failed: {exc}")
            kwargs["module"].send_group_message(kwargs["token"], group, text)
        kwargs["welcome"].mark_join_welcome(group)
        log(f"welcome join sent group={group[:8]} user={user[:8]} text={text!r}")
    except Exception as exc:
        log(f"welcome join failed group={group[:8]}: {exc}")


def _download_images(urls, message_id: str, group: str) -> list[Path]:
    paths: list[Path] = []
    for index, url in enumerate((urls or [])[:2]):
        target = ROOT / "logs" / "luna-images" / f"{message_id}-{index}.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        downloaded = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        downloaded.raise_for_status()
        target.write_bytes(downloaded.content)
        paths.append(target)
    if paths:
        log(f"luna: images={len(paths)} group={group[:8]}")
    return paths


def send_mention_reply(**kwargs) -> None:
    module = kwargs["module"]
    token = kwargs["token"]
    luna = kwargs["luna"]
    proactive = kwargs["proactive"]
    group = kwargs["group"]
    cooldown_key = kwargs["cooldown_key"]
    images = kwargs["images"]
    if not images and kwargs.get("image_urls"):
        try:
            images = _download_images(kwargs["image_urls"], kwargs["message_id"], group)
        except Exception as exc:
            log(f"luna mention image download failed: {exc}")
        if not images:
            try:
                module.send_group_message(
                    token, group, "……图没收到。重发一次。", msg_id=kwargs["message_id"]
                )
            except Exception as exc:
                log(f"luna image hint failed: {exc}")
            return
    tool = None
    if kwargs.get("tools_enabled") and kwargs.get("tools"):
        try:
            tool = kwargs["tools"].run(kwargs["plain"])
        except Exception as exc:
            log(f"tool failed key={cooldown_key}: {exc}")
    if tool:
        log(f"tool {tool[0]} key={cooldown_key} result={tool[1][:80]}")
        direct = kwargs["tool_message"](tool)
        if direct:
            try:
                module.send_group_message(token, group, direct, msg_id=kwargs["message_id"])
                log_reply(
                    group,
                    kwargs["user_id"],
                    kwargs["tier"],
                    kwargs["speaker_label"],
                    kwargs["plain"],
                    direct,
                )
                proactive.mark_replied(group)
            except Exception as exc:
                log(f"tool send failed: {exc}")
            return
    try:
        answer = luna.reply(
            group,
            kwargs["plain"],
            context=kwargs["context"],
            sister=kwargs["sister"],
            respect=kwargs["respect"],
            optional=not tool,
            source=kwargs["source"],
            profile=kwargs["profile"],
            tool_result=tool[1] if tool else "",
            speaker=kwargs["speaker"],
            role_card=kwargs["role_card"],
            privileged=kwargs["privileged"],
            role_name=kwargs["role_name"],
            target=kwargs["target"],
            target_card=kwargs["target_card"],
            target_is_sister=kwargs["target_is_sister"],
            images=images,
        )
    except Exception as exc:
        log(f"luna: addressed reply failed: {exc}")
        return
    if not answer:
        log(f"luna: skipped addressed key={cooldown_key}")
        return
    try:
        sent = module.send_group_message(
            token, group, answer, msg_id=kwargs["message_id"]
        )
        log(f"luna: addressed replied key={cooldown_key} id={(sent or {}).get('id')}")
        log_reply(
            group, kwargs["user_id"], kwargs["tier"], kwargs["speaker_label"], kwargs["plain"], answer
        )
        proactive.mark_replied(group)
    except Exception as exc:
        log(f"luna: addressed send failed: {exc}")


def send_command_reply(**kwargs) -> None:
    module = kwargs["module"]
    token = kwargs["token"]
    luna = kwargs["luna"]
    proactive = kwargs["proactive"]
    group = kwargs["group"]
    cooldown_key = kwargs["cooldown_key"]
    images = kwargs["images"]
    if not images and kwargs.get("image_urls"):
        try:
            images = _download_images(kwargs["image_urls"], kwargs["message_id"], group)
        except Exception as exc:
            log(f"luna image download failed: {exc}")
        if not images:
            try:
                module.send_group_message(
                    token, group, "……图没收到。重发一次。", msg_id=kwargs["message_id"]
                )
            except Exception as exc:
                log(f"luna image hint failed: {exc}")
            return
    tool = None
    if kwargs.get("tools_enabled") and kwargs.get("tools"):
        try:
            tool = kwargs["tools"].run(kwargs["args"])
        except Exception as exc:
            log(f"tool failed key={cooldown_key}: {exc}")
    if tool:
        log(f"tool {tool[0]} key={cooldown_key} result={tool[1][:80]}")
        direct = kwargs["tool_message"](tool)
        if direct:
            module.send_group_message(token, group, direct, msg_id=kwargs["message_id"])
            log_reply(
                group,
                kwargs["user_id"],
                kwargs["tier"],
                kwargs["speaker_label"],
                kwargs["args"],
                direct,
            )
            proactive.mark_replied(group)
            return
    try:
        answer = luna.reply(
            group,
            kwargs["args"],
            context=kwargs["context"],
            target=kwargs["target"],
            sister=kwargs["sister"],
            respect=kwargs["respect"],
            images=images,
            profile=kwargs["profile"],
            tool_result=tool[1] if tool else "",
            speaker=kwargs["speaker"],
            role_card=kwargs["role_card"],
            privileged=kwargs["privileged"],
            role_name=kwargs["role_name"],
            target_card=kwargs["target_card"],
            target_is_sister=kwargs["target_is_sister"],
        )
        sent = module.send_group_message(
            token, group, answer, msg_id=kwargs["message_id"]
        )
        log(
            f"luna replied key={cooldown_key} chars={len(answer)} "
            f"id={(sent or {}).get('id') if isinstance(sent, dict) else ''}"
        )
        log_reply(
            group, kwargs["user_id"], kwargs["tier"], kwargs["speaker_label"], kwargs["args"], answer
        )
        proactive.mark_replied(group)
    except Exception as exc:
        log(f"luna failed: {exc}")
        try:
            module.send_group_message(
                token, group, "……现在不想说话。", msg_id=kwargs["message_id"]
            )
        except Exception as send_exc:
            log(f"luna fallback failed: {send_exc}")


NAMES_PATH = LOG_DIR / "names.json"
OWNERS_PATH = LOG_DIR / "owners.json"


def load_owners() -> set:
    try:
        data = json.loads(OWNERS_PATH.read_text(encoding="utf-8"))
        return {str(item) for item in data if item}
    except Exception:
        return set()


def save_owners(owners: set) -> None:
    try:
        OWNERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        OWNERS_PATH.write_text(json.dumps(sorted(owners), ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass



def load_names() -> dict:
    try:
        data = json.loads(NAMES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_names(names: dict) -> None:
    try:
        NAMES_PATH.parent.mkdir(parents=True, exist_ok=True)
        NAMES_PATH.write_text(json.dumps(names, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def cache_mentions(names: dict, mentions) -> bool:
    changed = False
    for item in mentions or []:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("id") or item.get("member_openid") or "").strip()
        name = str(item.get("username") or item.get("nick") or "").strip()
        if uid and name and names.get(uid) != name:
            names[uid] = name
            changed = True
    return changed


def load_module(filename: str, alias: str):
    path = HERE / filename
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


STICKER_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def sticker_files() -> list[Path]:
    if not STICKER_DIR.exists():
        return []
    return [
        path
        for path in STICKER_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in STICKER_EXTS
    ]


_control_state: dict[str, bool] = {}


def control_enabled(name: str, default: bool = True) -> bool:
    """Live switches written by the web workbench. Missing file/key means default."""
    value = default
    try:
        data = json.loads(CONTROL_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and name in data:
            value = bool(data[name])
    except Exception:
        pass
    if _control_state.get(name) != value:
        _control_state[name] = value
        log(f"control {name}={'on' if value else 'off'}")
    return value


CHAT_START = 8 * 60
CHAT_END = 22 * 60 + 30
REPLY_LOG = LOG_DIR / "luna-replies.jsonl"
LAST_SENT_PATH = LOG_DIR / "last-sent.json"
LAST_SENT: dict[str, dict] = {}
_sent_lock = threading.Lock()


def load_last_sent() -> None:
    try:
        data = json.loads(LAST_SENT_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            LAST_SENT.update({str(k): v for k, v in data.items() if isinstance(v, dict)})
    except Exception:
        pass


def save_last_sent() -> None:
    try:
        LAST_SENT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _sent_lock:
            LAST_SENT_PATH.write_text(json.dumps(LAST_SENT, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def remember_sent(group: str, sent) -> None:
    if not isinstance(sent, dict) or not sent.get("id"):
        return
    LAST_SENT[str(group)] = {"id": str(sent["id"]), "ts": time.time()}
    save_last_sent()


def install_send_tracking(module) -> None:
    """包住发送函数，记下机器人最后一条消息的 ID。"""
    original_message = module.send_group_message
    original_image = module.send_group_image

    def send_group_message(token, group_openid, content, *args, **kwargs):
        sent = original_message(token, group_openid, content, *args, **kwargs)
        remember_sent(group_openid, sent)
        return sent

    def send_group_image(token, group_openid, path, file_name, *args, **kwargs):
        sent = original_image(token, group_openid, path, file_name, *args, **kwargs)
        remember_sent(group_openid, sent)
        return sent

    module.send_group_message = send_group_message
    module.send_group_image = send_group_image


def chat_window(now: datetime | None = None) -> bool:
    """自动聊天与被 @ 接话的时间窗：08:00-22:30。"""
    current = now or datetime.now()
    minutes = current.hour * 60 + current.minute
    return CHAT_START <= minutes < CHAT_END


TOOL_LINES = ("搜到了。自己看。", "就这些。", "拿去。", "喏。")


def tool_message(tool) -> str:
    """B站列表原样发，避免模型改写标题和链接。"""
    if tool and tool[0] == "bili":
        return random.choice(TOOL_LINES) + "\n\n" + tool[1]
    return ""


def log_reply(group: str, user: str, tier: str, name: str, question: str, answer: str) -> None:
    try:
        REPLY_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": int(time.time()),
            "group": group,
            "user": user,
            "tier": tier,
            "name": name,
            "question": str(question or "")[:200],
            "answer": str(answer or "")[:500],
        }
        with REPLY_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


_control_groups: dict[str, tuple[str, ...]] = {}
_registered_groups: set[str] = set()


def register_group(module, token_state: dict, group_openid: str) -> None:
    """Remember groups the bot is added to, so the workbench can list them."""
    group_openid = str(group_openid or "").strip()
    if not group_openid or group_openid in _registered_groups:
        return
    _registered_groups.add(group_openid)
    try:
        data = json.loads(GROUPS_PATH.read_text(encoding="utf-8")) if GROUPS_PATH.exists() else {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    entry = data.get(group_openid) if isinstance(data.get(group_openid), dict) else {}
    if entry.get("name"):
        return
    token = str(token_state.get("token") or "").strip()
    name = ""
    if token:
        try:
            info = module.group_request("GET", f"/v2/groups/{group_openid}/info", token) or {}
            name = str(info.get("group_name") or "").strip()
        except Exception as exc:
            log(f"group info failed group={group_openid[:8]}: {exc}")
    entry.update(
        {
            "name": name,
            "firstSeen": entry.get("firstSeen") or int(time.time()),
            "lastSeen": int(time.time()),
        }
    )
    data[group_openid] = entry
    try:
        GROUPS_PATH.parent.mkdir(parents=True, exist_ok=True)
        GROUPS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"group registered group={group_openid[:8]} name={name!r}")
    except Exception as exc:
        log(f"group registry save failed: {exc}")


def control_groups(name: str, default_groups: list[str]) -> list[str]:
    """Per-group selection written by the web workbench. Missing selection means default."""
    all_groups = list(default_groups)
    try:
        registry = json.loads(GROUPS_PATH.read_text(encoding="utf-8"))
        if isinstance(registry, dict):
            for group_openid in registry:
                if group_openid not in all_groups:
                    all_groups.append(group_openid)
    except Exception:
        pass

    saved = None
    try:
        data = json.loads(CONTROL_PATH.read_text(encoding="utf-8"))
        raw = (data.get("groups") or {}).get(name) if isinstance(data, dict) else None
        if isinstance(raw, list):
            saved = {str(item) for item in raw if item}
    except Exception:
        saved = None
    if saved is None:
        result = list(default_groups)
    else:
        result = [group for group in all_groups if group in saved]
    if _control_groups.get(name) != tuple(result):
        _control_groups[name] = tuple(result)
        log(f"control {name} groups={len(result)}")
    return result


class Progress:
    """只在请求变慢时才发进度提示，快的话一条都不发。"""

    def __init__(self, module, token: str, group_openid: str, message_id: str | None):
        self.module = module
        self.token = token
        self.group_openid = group_openid
        self.message_id = message_id
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._seq = 0
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        if self._stop.wait(PROGRESS_SEARCH_DELAY):
            return
        self.say("🔍 找图中…")
        if self._stop.wait(PROGRESS_DOWNLOAD_DELAY):
            return
        self.say("📥 图库里没合适的，正在下载…")

    def start(self) -> "Progress":
        self._thread.start()
        return self

    def next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def say(self, text: str) -> None:
        seq = self.next_seq()
        try:
            self.module.send_group_message(
                self.token, self.group_openid, text, msg_id=self.message_id, msg_seq=seq
            )
            log(f"progress seq={seq} text={text}")
        except Exception as exc:
            log(f"progress failed: {exc}")

    def finish(self) -> None:
        self._stop.set()


def group_state(module, token: str, group_openid: str) -> dict:
    cached = _group_states.get(group_openid)
    if cached and time.time() - cached[1] < BOT_STATE_TTL:
        return cached[0]
    state = {}
    try:
        state = module.group_request("GET", f"/v2/groups/{group_openid}/bot_state", token) or {}
    except Exception as exc:
        log(f"bot_state failed group={group_openid}: {exc}")
    _group_states[group_openid] = (state, time.time())
    return state


def bot_member_openid(module, token: str, group_openid: str) -> str:
    return str(group_state(module, token, group_openid).get("member_openid") or "").strip()


def install_token_retry(module, app_id: str, client_secret: str, state: dict) -> None:
    """Refresh the access token and retry once when QQ returns 401/11244."""

    def is_expired(exc: Exception) -> bool:
        text = str(exc)
        return "11244" in text or "40011027" in text or "401" in text

    def refresh() -> str:
        state["token"] = module.get_access_token(app_id, client_secret)
        state["ts"] = time.time()
        log("access_token 失效，已刷新")
        return state["token"]

    for name in ("send_group_message", "send_group_image", "send_group_file", "send_user_message", "mute_group_member"):
        original = getattr(module, name)

        def wrapper(token, *args, _fn=original, **kwargs):
            try:
                return _fn(token, *args, **kwargs)
            except Exception as exc:
                if not is_expired(exc):
                    raise
                return _fn(refresh(), *args, **kwargs)

        setattr(module, name, wrapper)

    original_request = module.group_request

    def request(method, path, token, **kwargs):
        try:
            return original_request(method, path, token, **kwargs)
        except Exception as exc:
            if not is_expired(exc):
                raise
            return original_request(method, path, refresh(), **kwargs)

    module.group_request = request
    state.setdefault("token", "")
    state.setdefault("ts", 0.0)


def parse_image_request(content: str):
    text = strip_mentions(content)
    if not text:
        return None
    if text.isdigit():
        return text, 1
    matched = re.fullmatch(r"(.*?)\s*([0-9]{1,2})", text)
    if matched and matched.group(1).strip():
        name, count = matched.group(1).strip(), int(matched.group(2))
    else:
        name, count = text, 1
    parts = name.split()
    if len(parts) > 1:
        underscore = [part for part in parts if "_" in part]
        has_chinese = any(re.search(r"[\u4e00-\u9fa5]", part) for part in parts)
        if len(underscore) == 1 and has_chinese:
            name = underscore[0]
    return name, count


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stream = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream

    module = load_module("qq-group.py", "qq_group")
    load_last_sent()
    install_send_tracking(module)
    images = load_module("image_source.py", "image_source")
    chat = load_module("chat_log.py", "chat_log")
    summary = load_module("chat_summary.py", "chat_summary")
    jelly = load_module("qq-jelly.py", "qq_jelly")
    help_card = load_module("qq-help-card.py", "help_card")
    help_menu = load_module("luna_help_menu.py", "luna_help_menu")
    weather = load_module("luna_weather.py", "luna_weather")
    luna = load_module("luna_chat.py", "luna_chat")
    roles = load_module("luna_roles.py", "luna_roles")
    proactive = load_module("luna_proactive.py", "luna_proactive")
    welcome = load_module("luna_welcome.py", "luna_welcome")
    bili = load_module("luna_bili.py", "luna_bili")
    channel = load_module("luna_channel.py", "luna_channel")
    memory = load_module("memory.py", "memory")
    reminders = load_module("reminders.py", "reminders")
    tools = load_module("tools.py", "tools")
    night = load_module("luna_night.py", "luna_night")
    holiday = load_module("luna_holiday.py", "luna_holiday")
    guard = load_module("input_guard.py", "input_guard")
    profiles = load_module("profiles.py", "profiles")
    config = module.load_config(module.CONFIG)
    app_id = str(config.get("appId", "")).strip()
    client_secret = str(config.get("clientSecret", "")).strip()
    sister_openid = str(config.get("sisterOpenid") or config.get("userOpenid") or "").strip()
    if not app_id or not client_secret:
        log("config 缺少 appId/clientSecret")
        return 1

    token_state = {"token": "", "ts": 0.0}
    groups = []
    for key in ("groupOpenid", "testGroupOpenids", "dailyGroupOpenids", "extraGroupOpenids"):
        value = config.get(key)
        items = value if isinstance(value, list) else [value]
        for item in items:
            gid = str(item or "").strip()
            if gid and gid not in groups:
                groups.append(gid)

    exclude = {str(item).strip() for item in (config.get("proactiveGroupsExclude") or []) if str(item).strip()}
    proactive_groups = [gid for gid in groups if gid not in exclude]

    proactive_default = bool(config.get("proactive", True))
    if proactive_groups:
        def proactive_loop():
            time.sleep(20)
            while True:
                try:
                    if (
                        not control_enabled("pause", False)
                        and control_enabled("chat", True)
                        and control_enabled("proactive", proactive_default)
                    ):
                        targets = control_groups("proactive", proactive_groups)
                        if targets:
                            proactive.run_once(module, targets, token_state, sister_openid)
                except Exception as exc:
                    log(f"proactive loop failed: {exc}")
                time.sleep(180)

        threading.Thread(target=proactive_loop, daemon=True).start()
        log(f"proactive loop started groups={len(proactive_groups)}")

    try:
        for gid in groups:
            seen_count = welcome.seed(gid)
            log(f"welcome seed group={gid[:8]} members={seen_count}")
    except Exception as exc:
        log(f"welcome seed failed: {exc}")

    try:
        added = roles.seed(sister_openid, groups)
        log(f"roles seed sister=alpha groups_added={added}")
    except Exception as exc:
        log(f"roles seed failed: {exc}")

    bili_groups = config.get("biliGroups")
    if not isinstance(bili_groups, list):
        bili_groups = [gid for gid in groups if gid != str(config.get("groupOpenid", "")).strip()]

    bili_default = bool(config.get("bili", True))
    if bili_groups:
        def bili_loop():
            time.sleep(60)
            last_run = 0.0
            while True:
                try:
                    if (
                        not control_enabled("pause", False)
                        and control_enabled("bili", bili_default)
                        and time.time() - last_run >= 1800
                    ):
                        targets = control_groups("bili", bili_groups)
                        if targets:
                            last_run = time.time()
                            bili.run_once(module, targets, token_state)
                except Exception as exc:
                    log(f"bili loop failed: {exc}")
                time.sleep(60)

        threading.Thread(target=bili_loop, daemon=True).start()
        log(f"bili loop started groups={len(bili_groups)}")

    def channel_loop():
        time.sleep(90)
        while True:
            try:
                if not control_enabled("pause", False) and control_enabled("channelPost", True):
                    for item in channel.run_once():
                        log(f"channel {item.get('slot') or 'comment'}: {item.get('status')}")
            except Exception as exc:
                log(f"channel loop failed: {exc}")
            time.sleep(300)

    threading.Thread(target=channel_loop, daemon=True).start()
    log("channel post loop started")

    def memory_loop():
        time.sleep(20)
        while True:
            try:
                memory.index_all()
                memory.update_all_summaries()
            except Exception as exc:
                log(f"memory index failed: {exc}")
            time.sleep(300)

    threading.Thread(target=memory_loop, daemon=True).start()
    log("memory index loop started")

    def reminder_loop():
        time.sleep(5)
        while True:
            try:
                due = reminders.take_due()
                if due:
                    reminder_token = module.get_access_token(app_id, client_secret)
                    for item in due:
                        group = str(item.get("group", ""))
                        user = str(item.get("user") or "")
                        text = str(item.get("text") or "")
                        content = f"时间到了。{text}。"
                        sent = False
                        if user:
                            try:
                                module.send_group_markdown(
                                    reminder_token,
                                    group,
                                    f'<qqbot-at-user id="{user}" /> {content}',
                                )
                                sent = True
                            except Exception as exc:
                                log(f"reminder markdown failed: {exc}")
                        if not sent:
                            try:
                                module.send_group_message(
                                    reminder_token,
                                    group,
                                    f"<@{user}> {content}" if user else content,
                                )
                                sent = True
                            except Exception as exc:
                                log(f"reminder send failed: {exc}")
                        if sent:
                            log(f"reminder sent group={group[:8]} user={user[:8]}")
            except Exception as exc:
                log(f"reminder loop failed: {exc}")
            time.sleep(3)

    threading.Thread(target=reminder_loop, daemon=True).start()
    log("reminder loop started")

    night_default = bool(config.get("goodnight", True))
    if groups:
        def night_loop():
            while True:
                try:
                    if not control_enabled("pause", False) and control_enabled("goodnight", night_default):
                        targets = control_groups("goodnight", groups)
                        if targets:
                            night.run_once(module, targets, token_state)
                except Exception as exc:
                    log(f"night loop failed: {exc}")
                time.sleep(30)

        threading.Thread(target=night_loop, daemon=True).start()
        log(f"goodnight loop started groups={len(groups)}")

    holiday_default = bool(config.get("holiday", True))
    if groups:
        def holiday_loop():
            while True:
                try:
                    if not control_enabled("pause", False) and control_enabled("holiday", holiday_default):
                        targets = control_groups("holiday", groups)
                        if targets:
                            holiday.run_once(module, targets, token_state)
                except Exception as exc:
                    log(f"holiday loop failed: {exc}")
                time.sleep(60)

        threading.Thread(target=holiday_loop, daemon=True).start()
        log(f"holiday loop started groups={len(groups)}")
    install_token_retry(module, app_id, client_secret, token_state)

    names = load_names()
    owners = load_owners()
    log("bot starting")
    last_image_reply = {}
    last_hint = {}
    last_notice = {}
    last_summary = {}
    last_jelly = {}
    last_jelly_hint = {}
    last_help = {}
    last_unknown = {}
    last_weather = {}
    last_weather_hint = {}
    last_sticker = {}
    last_sticker_pick = {}
    last_luna = {}
    task_hits = {}
    violence_hits = {}
    penalties = {}
    recent_texts = {}
    inflight = set()
    settings_logged = False
    while True:
        ws = None
        try:
            token = module.get_access_token(app_id, client_secret)
            token_state["token"] = token
            token_state["ts"] = time.time()
            gateway_url, shards = module.get_gateway(token)
            ws, interval, last_seq = module.open_gateway(gateway_url, token, shards, module.GROUP_INTENTS)
            log("gateway connected")
            if not settings_logged:
                settings_logged = True
                known = {str(config.get("groupOpenid", "")).strip()}
                for key in ("testGroupOpenids", "dailyGroupOpenids", "extraGroupOpenids"):
                    known.update(str(item) for item in config.get(key) or [])
                for gid in sorted(item for item in known if item):
                    state = group_state(module, token, gid)
                    log(f"group {gid[:8]} recv_msg_setting={state.get('recv_msg_setting')}")
            last_heartbeat = time.time()
            seen = set()
            while True:
                if time.time() - last_heartbeat >= interval:
                    ws.send(json.dumps({"op": 1, "d": last_seq}))
                    last_heartbeat = time.time()
                if time.time() - token_state["ts"] > 1500:
                    token = module.get_access_token(app_id, client_secret)
                    token_state["token"] = token
                    token_state["ts"] = time.time()
                    log("access_token 主动刷新")
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                message = json.loads(raw)
                if message.get("s") is not None:
                    last_seq = message["s"]
                op = message.get("op")
                if op == 9:
                    log(f"gateway closed: {message}")
                    break
                if op == 7:
                    log("gateway requested reconnect")
                    break
                event = message.get("t")
                if event == "GROUP_MEMBER_ADD":
                    data = message.get("d", {})
                    group_openid = str(data.get("group_openid") or "").strip()
                    member_openid = str(data.get("member_openid") or "").strip()
                    if not group_openid or not member_openid:
                        continue
                    register_group(module, token_state, group_openid)
                    if (
                        control_enabled("pause", False)
                        or not control_enabled("welcome", True)
                        or member_openid == sister_openid
                    ):
                        continue
                    bot_id = bot_member_openid(module, token, group_openid)
                    if member_openid == bot_id:
                        continue
                    if not welcome.is_new(group_openid, member_openid):
                        continue
                    if welcome.join_too_soon(group_openid):
                        log(f"welcome join: too soon group={group_openid[:8]}")
                        continue
                    payload = dict(
                        module=module,
                        token=token,
                        welcome=welcome,
                        group=group_openid,
                        user_id=member_openid,
                    )
                    submit_reply(
                        group_openid, "welcome-join", functools.partial(send_join_welcome, **payload)
                    )
                    continue

                if event == "GROUP_ADD_ROBOT":
                    data = message.get("d", {})
                    register_group(module, token_state, data.get("group_openid", ""))
                    continue
                if event not in ("GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"):
                    continue

                data = message.get("d", {})
                group_openid = str(data.get("group_openid", "")).strip()
                message_id = data.get("id")
                if not group_openid or not message_id or message_id in seen:
                    continue
                register_group(module, token_state, group_openid)
                seen.add(message_id)
                if len(seen) > 500:
                    seen = set(list(seen)[-200:])

                content = str(data.get("content", ""))
                author = data.get("author") or {}
                user_id = str(author.get("member_openid") or author.get("id") or "").strip()
                cooldown_key = user_id or group_openid
                bot_id = bot_member_openid(module, token, group_openid)
                if cache_mentions(names, data.get("mentions")):
                    save_names(names)
                has_image = any(
                    str(item.get("content_type") or "").lower().startswith("image")
                    for item in (data.get("attachments") or [])
                ) or bool(jelly.deep_image_urls(data))
                direct_call = bool(re.match(r"^\s*露娜\s*[，,：:！!]\s*\S+", content or ""))
                implicit_luna = (bool(has_image) and "露娜" in content) or direct_call
                command = parse_command(content)
                if not command and implicit_luna:
                    args = re.sub(r"^[\s露娜,，:：！!]+", "", content).strip()
                    command = ("露娜", args or "看看这张图")
                is_command = bool(command)
                chat_on = control_enabled("chat", True)
                role = str(author.get("member_role") or "").strip().lower()
                role_card = roles.resolve(group_openid, user_id, sister_openid)
                role_name = roles.display(role_card)
                is_sister_line = (bool(sister_openid) and user_id == sister_openid) or roles.is_sister(role_card)
                is_privileged = (bool(sister_openid) and user_id == sister_openid) or roles.is_privileged(role_card)
                tier = "sister" if is_sister_line else ("owner" if role == "owner" or user_id in owners else "member")
                if role_card:
                    log(f"role speaker={role_name or role_card.get('id')} tier={role_card.get('tier')}")
                speaker = str(
                    author.get("username") or author.get("card") or names.get(user_id) or ""
                ).strip()

                if user_id and user_id != bot_id and not is_sister_line and control_enabled("guard", True):
                    blocked, reason = guard.flood(group_openid, user_id)
                    if blocked:
                        log(f"guard flood group={group_openid[:8]} user={user_id[:8]} reason={reason}")
                        continue

                if user_id and user_id != bot_id and not is_command:
                    try:
                        chat.record(
                            group_openid,
                            user_id,
                            chat.clean_text(content, data.get("attachments")),
                            speaker,
                            role,
                            [
                                str(item.get("url") or "")
                                for item in (data.get("attachments") or [])
                                if str(item.get("content_type") or "").lower().startswith("image")
                            ],
                        )
                        if role == "owner" and user_id not in owners:
                            owners.add(user_id)
                            save_owners(owners)
                            log(f"owner cached group={group_openid[:8]} user={user_id}")
                        if control_enabled("profile", True):
                            profiles.touch(
                                group_openid,
                                user_id,
                                speaker,
                                tier,
                                chat.clean_text(content, data.get("attachments")),
                            )
                    except Exception as exc:
                        log(f"chat record failed: {exc}")

                if control_enabled("pause", False):
                    continue

                if (
                    user_id
                    and user_id != bot_id
                    and not is_command
                    and tier == "member"
                    and event != "GROUP_AT_MESSAGE_CREATE"
                    and "露娜" not in content
                    and (not bot_id or f"<@{bot_id}>" not in content)
                    and control_enabled("welcome", True)
                    and welcome.is_new(group_openid, user_id)
                    and welcome.size(group_openid) >= welcome.MIN_MEMBERS
                    and not welcome.too_soon(group_openid)
                ):
                    payload = dict(
                        module=module,
                        token=token,
                        welcome=welcome,
                        group=group_openid,
                        message_id=message_id,
                        speaker=speaker,
                        user_id=user_id,
                    )
                    submit_reply(
                        group_openid, "welcome", functools.partial(send_welcome, **payload)
                    )

                if event == "GROUP_MESSAGE_CREATE":
                    member = bot_id
                    if (not member or (f"<@{member}>" not in content and f"<@!{member}>" not in content)) and not implicit_luna:
                        continue
                log(f"at group={group_openid} user={user_id or '-'} event={event} content={content!r}")

                if user_id and penalties.get(user_id, 0.0) > time.time():
                    log(f"penalty: ignore user={user_id} remain={int(penalties[user_id] - time.time())}s")
                    continue

                addressed = (
                    event == "GROUP_AT_MESSAGE_CREATE"
                    or (bool(bot_id) and (f"<@{bot_id}>" in content or f"<@!{bot_id}>" in content))
                    or "露娜" in content
                )

                if not command:
                    if not addressed:
                        continue
                    if not chat_on:
                        log(f"luna: chat disabled, skip addressed key={cooldown_key}")
                        continue
                    plain = chat.clean_text(content, data.get("attachments"))
                    target_id = ""
                    for item in data.get("mentions") or []:
                        uid = str(item.get("id") or item.get("member_openid") or "").strip()
                        if uid and uid != bot_id:
                            target_id = uid
                            break
                    if not target_id:
                        matched = re.search(r"<@!?([0-9A-Za-z_-]{10,})>", content or "")
                        if matched and matched.group(1) != bot_id:
                            target_id = matched.group(1)
                    target_name = (
                        chat.user_name(group_openid, target_id) or names.get(target_id, "")
                    ) if target_id else ""
                    target_role = roles.resolve(group_openid, target_id, sister_openid) if target_id else ""
                    if target_role:
                        log(f"luna: target role={roles.display(target_role)} target={target_name or target_id[:8]}")
                    if is_sister_line and control_enabled("reminder", True):
                        parsed = reminders.maybe_create(group_openid, user_id, speaker, plain)
                        if parsed:
                            too_short = bool(parsed.get("too_short"))
                            answer = (
                                random.choice(REMINDER_TOO_SHORT_LINES)
                                if too_short
                                else f"记下了。{reminders.format_due(parsed['due'])}，{parsed['text']}。"
                            )
                            try:
                                module.send_group_message(token, group_openid, answer, msg_id=message_id)
                                if too_short:
                                    log(f"reminder too short key={cooldown_key}")
                                else:
                                    log(f"reminder created key={cooldown_key} due={parsed['due']}")
                                log_reply(group_openid, user_id, tier, speaker, plain, answer)
                            except Exception as exc:
                                log(f"reminder reply failed: {exc}")
                            continue
                    if not chat_window():
                        log(f"luna: outside window, skip addressed key={cooldown_key}")
                        continue
                    if control_enabled("guard", True):
                        ok, reason = guard.content(plain)
                        if not ok:
                            log(f"guard content group={group_openid[:8]} user={user_id[:8]} reason={reason}")
                            guard.record(group_openid, user_id, reason, plain)
                            continue
                    if not is_sister_line and luna.is_sexual_violence(plain):
                        log(f"luna: sexual violence key={cooldown_key} text={plain[:40]!r}")
                        punish(
                            module,
                            token,
                            group_openid,
                            user_id,
                            message_id,
                            penalties,
                            random.choice(luna.SEXUAL_VIOLENCE_LINES),
                            minutes=10,
                        )
                        continue
                    now = time.time()
                    sister_identity = is_sister_line and luna.is_identity_question(plain)
                    if not sister_identity and now - last_luna.get(cooldown_key, 0.0) < LUNA_COOLDOWN:
                        log(f"luna: addressed cooldown key={cooldown_key}")
                        continue
                    last_luna[cooldown_key] = now
                    recent = chat.history(group_openid, limit=6)
                    recent_rows = []
                    for item in recent:
                        item_role = roles.resolve(
                            group_openid, str(item.get("user", "")), sister_openid
                        )
                        if item_role.get("id") == "alpha":
                            item_name = "姐姐"
                        elif roles.is_sister(item_role):
                            item_name = "另一位姐姐"
                        elif item_role:
                            item_name = roles.display(item_role) or item.get("name") or "群友"
                        else:
                            item_name = item.get("name") or "群友"
                        recent_rows.append(f"{item_name}：{str(item.get('text', ''))[:60]}")
                    context_lines = "\n".join(recent_rows)
                    speaker_profile = (
                        profiles.summary(user_id)
                        if control_enabled("profile", True) and not role_card
                        else ""
                    )
                    if (
                        target_id
                        and target_id != user_id
                        and control_enabled("profile", True)
                        and not target_role
                    ):
                        target_profile = profiles.summary(target_id)
                        if target_profile:
                            speaker_profile = f"{speaker_profile}\n被 @ 的人：{target_profile}".strip()
                    reply_speaker = "姐姐" if is_sister_line else (role_name or speaker)
                    target_display = (
                        "另一位姐姐"
                        if roles.is_sister(target_role)
                        else (roles.display(target_role) or target_name)
                    ) if target_role else target_name
                    image_urls = []
                    if has_image:
                        image_urls = (
                            jelly.image_urls(data.get("attachments"))[:2]
                            or jelly.deep_image_urls(data)[:2]
                        )
                        if not image_urls:
                            try:
                                module.send_group_message(
                                    token, group_openid, "……图没收到。重发一次。", msg_id=message_id
                                )
                            except Exception as exc:
                                log(f"luna image hint failed: {exc}")
                            continue
                    payload = dict(
                        module=module,
                        token=token,
                        luna=luna,
                        proactive=proactive,
                        group=group_openid,
                        cooldown_key=cooldown_key,
                        plain=plain,
                        context=context_lines,
                        sister=is_sister_line,
                        respect=tier == "owner" or user_id in owners or luna.is_respected(speaker),
                        source="mention",
                        profile=speaker_profile,
                        tools=tools,
                        tools_enabled=control_enabled("tools", True),
                        tool_message=tool_message,
                        speaker=reply_speaker,
                        speaker_label=speaker,
                        role_card=roles.card_text(role_card),
                        privileged=is_privileged,
                        role_name=role_name,
                        target=target_display,
                        target_card=roles.target_card_text(target_role),
                        target_is_sister=roles.is_sister(target_role),
                        images=[],
                        image_urls=image_urls,
                        message_id=message_id,
                        user_id=user_id,
                        tier=tier,
                    )
                    submit_reply(
                        group_openid, "mention", functools.partial(send_mention_reply, **payload)
                    )
                    continue
                cmd, args = command
                log(f"command={cmd} args={args!r} group={group_openid}")

                if cmd in ("天气", "weather"):
                    now = time.time()
                    city = args.strip()
                    keyboard = weather_keyboard()
                    if not city:
                        if now - last_weather_hint.get(group_openid, 0.0) < 5:
                            continue
                        last_weather_hint[group_openid] = now
                        text = (
                            "**⚠️ 您还没有输入地区哦**\n\n"
                            "使用说明：\n`/天气 [地名]`\n\n"
                            "示例：\n`/天气 北京`"
                        )
                        try:
                            module.send_group_markdown(
                                token,
                                group_openid,
                                text,
                                keyboard=keyboard,
                                msg_id=message_id,
                                msg_seq=1,
                            )
                        except Exception as exc:
                            log(f"weather help failed: {exc}")
                            try:
                                module.send_group_message(
                                    token,
                                    group_openid,
                                    "用法：/天气 [地名]\n示例：/天气 北京",
                                    msg_id=message_id,
                                )
                            except Exception as send_exc:
                                log(f"weather help text failed: {send_exc}")
                        continue
                    if now - last_weather.get(group_openid, 0.0) < 10:
                        log(f"weather: cooldown group={group_openid[:8]}")
                        continue
                    last_weather[group_openid] = now
                    try:
                        data = weather.fetch(city)
                        path = weather.render(data)
                        try:
                            module.send_group_image(
                                token,
                                group_openid,
                                path,
                                path.name,
                                msg_id=message_id,
                                msg_seq=1,
                                keyboard=keyboard,
                            )
                        except Exception as keyboard_exc:
                            log(f"weather keyboard image failed: {keyboard_exc}")
                            module.send_group_image(
                                token,
                                group_openid,
                                path,
                                path.name,
                                msg_id=message_id,
                                msg_seq=1,
                            )
                        log(f"weather sent city={city} group={group_openid[:8]}")
                    except Exception as exc:
                        log(f"weather failed city={city}: {exc}")
                        try:
                            module.send_group_message(
                                token,
                                group_openid,
                                f"没查到「{city}」的天气。",
                                msg_id=message_id,
                            )
                        except Exception as send_exc:
                            log(f"weather error send failed: {send_exc}")
                    continue

                if cmd in ("随机表情包", "表情包", "随机表情"):
                    now = time.time()
                    if now - last_sticker.get(cooldown_key, 0.0) < 10:
                        log(f"sticker: cooldown key={cooldown_key}")
                        continue
                    last_sticker[cooldown_key] = now
                    files = sticker_files()
                    if not files:
                        try:
                            module.send_group_message(
                                token, group_openid, "表情包文件夹是空的。", msg_id=message_id
                            )
                        except Exception as exc:
                            log(f"sticker empty hint failed: {exc}")
                        continue
                    pick = random.choice(files)
                    last_pick = last_sticker_pick.get(group_openid)
                    if len(files) > 1 and pick == last_pick:
                        pick = random.choice([path for path in files if path != last_pick])
                    try:
                        module.send_group_image(
                            token, group_openid, pick, pick.name, msg_id=message_id, msg_seq=1
                        )
                        last_sticker_pick[group_openid] = pick
                        log(f"sticker: sent {pick.name} key={cooldown_key}")
                    except Exception as exc:
                        log(f"sticker send failed: {exc}")
                    continue

                if cmd in ("帮助", "help", "菜单", "使用说明"):
                    now = time.time()
                    if now - last_help.get(group_openid, 0.0) >= HELP_COOLDOWN:
                        last_help[group_openid] = now
                        menu_text, menu_keyboard = help_menu.build()
                        try:
                            module.send_group_markdown(
                                token,
                                group_openid,
                                menu_text,
                                keyboard=menu_keyboard,
                                msg_id=message_id,
                                msg_seq=1,
                            )
                            log(f"help markdown+keyboard sent group={group_openid}")
                        except Exception as exc:
                            log(f"help keyboard failed: {exc}")
                            try:
                                module.send_group_markdown(
                                    token,
                                    group_openid,
                                    menu_text,
                                    msg_id=message_id,
                                    msg_seq=1,
                                )
                                log(f"help markdown sent group={group_openid}")
                            except Exception as markdown_exc:
                                log(f"help markdown failed: {markdown_exc}")
                                try:
                                    card = help_card.build()
                                    module.send_group_image(
                                        token, group_openid, card, card.name, msg_id=message_id, msg_seq=1
                                    )
                                    module.send_group_message(
                                        token,
                                        group_openid,
                                        f"使用说明：{HELP_URL}",
                                        msg_id=message_id,
                                        msg_seq=2,
                                    )
                                    log(f"help card+link sent group={group_openid}")
                                except Exception as card_exc:
                                    log(f"help send failed: {card_exc}")
                                    try:
                                        module.send_group_message(
                                            token, group_openid, HELP_TEXT, msg_id=message_id
                                        )
                                    except Exception as send_exc:
                                        log(f"help text failed: {send_exc}")
                    continue

                if cmd in ("撤回", "recall"):
                    if not is_sister_line:
                        continue
                    entry = LAST_SENT.get(group_openid) or {}
                    message_id = str(entry.get("id") or "")
                    sent_at = float(entry.get("ts") or 0)
                    if not message_id:
                        module.send_group_message(
                            token, group_openid, "没有可撤回的消息。", msg_id=None, msg_seq=1
                        )
                        continue
                    if time.time() - sent_at > 115:
                        module.send_group_message(
                            token, group_openid, "超过两分钟，撤不回了。", msg_id=None
                        )
                        continue
                    try:
                        module.recall_group_message(token, group_openid, message_id)
                        LAST_SENT.pop(group_openid, None)
                        save_last_sent()
                        module.send_group_message(token, group_openid, "撤了。", msg_id=None, msg_seq=2)
                        log(f"recalled last message group={group_openid[:8]}")
                    except Exception as exc:
                        log(f"recall failed: {exc}")
                        module.send_group_message(token, group_openid, "撤不回了。", msg_id=None)
                    continue

                if cmd == "聊天总结":
                    if not control_enabled("summary", True):
                        log(f"summary: disabled group={group_openid}")
                        continue
                    now = time.time()
                    if now - last_summary.get(group_openid, 0.0) < SUMMARY_COOLDOWN:
                        log(f"summary: cooldown group={group_openid}")
                        continue
                    try:
                        card = summary.build(group_openid)
                        result = module.send_group_image(
                            token, group_openid, card, card.name, msg_id=message_id
                        )
                        last_summary[group_openid] = time.time()
                        log(f"summary sent {card.name} id={result.get('id')}")
                    except ValueError as exc:
                        log(f"summary: not enough data: {exc}")
                        setting = str(group_state(module, token, group_openid).get("recv_msg_setting") or "")
                        if setting and setting != "all":
                            text = (
                                f"今天群里才 {exc} 条消息。本群机器人接收设置是「仅@消息」，"
                                "让群主在 群设置 → 机器人 里改成「接收全部消息」，我才能记录群聊。"
                            )
                        else:
                            text = f"今天群里才 {exc} 条消息，多聊几句再来总结。"
                        try:
                            module.send_group_message(token, group_openid, text, msg_id=message_id)
                            log(f"summary hint sent group={group_openid} count={exc}")
                        except Exception as send_exc:
                            log(f"summary hint failed: {send_exc}")
                    except Exception as exc:
                        log(f"summary failed: {exc}")
                    continue

                if cmd == "对着露娜发电":
                    if not control_enabled("despise", True):
                        log(f"despise: disabled group={group_openid}")
                        continue
                    state = _despise.setdefault(cooldown_key, {"count": 0, "last": 0.0, "until": 0.0})
                    now = time.time()
                    if state["until"] > now:
                        log(f"despise: cooldown key={cooldown_key} remain={int(state['until'] - now)}s")
                        continue
                    if now - state.get("last", 0.0) > DESPISE_RESET:
                        state["count"] = 0
                    if now - state.get("last", 0.0) < DESPISE_GAP:
                        continue
                    state["count"] = state["count"] % 3 + 1
                    state["last"] = now
                    level = state["count"]
                    label = DESPISE_LEVELS[level - 1]
                    sticker = STICKER_DIR / f"sticker-{level}.jpg"
                    if sticker.exists():
                        try:
                            module.send_group_image(token, group_openid, sticker, sticker.name, msg_id=message_id)
                            log(f"despise: {label} ({level}/3) key={cooldown_key}")
                        except Exception as exc:
                            log(f"despise send failed: {exc}")
                    if state["count"] >= 3:
                        state["count"] = 0
                        state["until"] = now + DESPISE_COOLDOWN
                        try:
                            module.mute_group_member(token, group_openid, user_id, 10)
                            log(f"despise: muted user={user_id} for 10min")
                        except Exception as exc:
                            log(f"despise: mute failed user={user_id}: {exc}")
                        try:
                            module.send_group_message(
                                token,
                                group_openid,
                                random.choice(DESPISE_LINES),
                                msg_id=message_id,
                                msg_seq=2,
                            )
                        except Exception as exc:
                            log(f"despise: line send failed: {exc}")
                    continue

                if cmd.lower() in ("q弹", "q"):
                    if not control_enabled("jelly", True):
                        log(f"jelly: disabled group={group_openid}")
                        continue
                    now = time.time()
                    if now - last_jelly.get(cooldown_key, 0.0) < JELLY_COOLDOWN:
                        log(f"jelly: cooldown key={cooldown_key}")
                        continue
                    if cooldown_key in inflight:
                        log(f"jelly: busy key={cooldown_key}")
                        continue
                    urls = jelly.deep_image_urls(data)
                    if not urls:
                        try:
                            path = jelly.dump_event(data)
                            log(f"jelly: no image type={data.get('message_type')} dumped={path.name}")
                        except Exception as exc:
                            log(f"jelly: dump failed: {exc}")
                        if now - last_jelly_hint.get(cooldown_key, 0.0) >= JELLY_HINT_COOLDOWN:
                            last_jelly_hint[cooldown_key] = now
                            try:
                                module.send_group_message(
                                    token,
                                    group_openid,
                                    "Q弹需要一张图：@我 + 图片 +「Q弹」。"
                                    "官方接口读不到群成员头像，拿不到别人的头像。",
                                    msg_id=message_id,
                                )
                            except Exception as exc:
                                log(f"jelly hint failed: {exc}")
                        continue
                    inflight.add(cooldown_key)
                    try:
                        path = jelly.generate(urls[0])
                        result = module.send_group_image(
                            token, group_openid, path, path.name, msg_id=message_id
                        )
                        last_jelly[cooldown_key] = time.time()
                        log(f"jelly sent {path.name} id={result.get('id')}")
                    except Exception as exc:
                        log(f"jelly failed: {exc}")
                        try:
                            module.send_group_message(
                                token, group_openid, "这张图揉不动，换一张试试。", msg_id=message_id
                            )
                        except Exception as send_exc:
                            log(f"jelly notice failed: {send_exc}")
                    finally:
                        inflight.discard(cooldown_key)
                        jelly.prune()
                    continue

                if cmd == "露娜":
                    if not chat_on:
                        log(f"luna: chat disabled, skip command key={cooldown_key}")
                        continue
                    now = time.time()
                    sister_identity = is_sister_line and luna.is_identity_question(args)
                    if not sister_identity and now - last_luna.get(cooldown_key, 0.0) < LUNA_COOLDOWN:
                        log(f"luna: cooldown key={cooldown_key}")
                        continue

                    if is_sister_line and control_enabled("reminder", True):
                        parsed = reminders.maybe_create(group_openid, user_id, speaker, args)
                        if parsed:
                            too_short = bool(parsed.get("too_short"))
                            answer = (
                                random.choice(REMINDER_TOO_SHORT_LINES)
                                if too_short
                                else f"记下了。{reminders.format_due(parsed['due'])}，{parsed['text']}。"
                            )
                            try:
                                module.send_group_message(token, group_openid, answer, msg_id=message_id)
                                if too_short:
                                    log(f"reminder too short key={cooldown_key}")
                                else:
                                    log(f"reminder created key={cooldown_key} due={parsed['due']}")
                                log_reply(group_openid, user_id, tier, speaker, args, answer)
                            except Exception as exc:
                                log(f"reminder reply failed: {exc}")
                            continue

                    target_id = ""
                    target_name = ""
                    for item in data.get("mentions") or []:
                        uid = str(item.get("id") or item.get("member_openid") or "").strip()
                        if uid and uid != bot_id:
                            target_id = uid
                            target_name = str(item.get("username") or "").strip()
                            break
                    if not target_id:
                        matched = re.search(r"<@!?([0-9A-Za-z_-]{10,})>", args)
                        if matched and matched.group(1) != bot_id:
                            target_id = matched.group(1)

                    context = ""
                    quote = extract_quote(data)
                    target_role = roles.resolve(group_openid, target_id, sister_openid) if target_id else {}
                    if target_id:
                        entries = chat.user_history(group_openid, target_id, limit=30)
                        target_name = target_name or chat.user_name(group_openid, target_id) or names.get(target_id, "")
                        if not entries and not quote and not target_role:
                            try:
                                module.send_group_message(
                                    token,
                                    group_openid,
                                    "……我对他没什么印象。他几乎没说过话。",
                                    msg_id=message_id,
                                )
                            except Exception as exc:
                                log(f"luna target hint failed: {exc}")
                            continue
                        parts = []
                        if entries:
                            parts.append("\n".join(f"- {str(item.get('text', ''))[:80]}" for item in entries[-30:]))
                        if quote:
                            parts.append(f"（他刚被引用/提到的内容：{quote}）")
                        context = "\n".join(parts)
                        keep = target_name or "对方"
                        args = re.sub(r"<@!?[^>]+>", f" {keep} ", args).strip() or "评价一下这个人"
                        args = re.sub(r"\s+", " ", args)
                        log(f"luna: context user={target_id} lines={len(entries)} quote={bool(quote)}")
                    elif quote:
                        context = f"（对方引用了一条消息：{quote}）"
                        log(f"luna: quote context only lines=0")

                    if quote and not args:
                        args = "回应一下引用的这条消息"

                    if luna.is_capability(args):
                        if now - last_help.get(group_openid, 0.0) < HELP_COOLDOWN:
                            log(f"luna: capability cooldown group={group_openid}")
                            continue
                        last_help[group_openid] = now
                        line = "……姐姐想看的话，都在这上面。" if is_sister_line else random.choice(
                            ["……自己看。别让我重复第二遍。", "都写在上面了，人类。", "问这种问题之前，先学会自己看。"]
                        )
                        try:
                            module.send_group_message(
                                token, group_openid, line, msg_id=message_id, msg_seq=1
                            )
                            card = help_card.build()
                            module.send_group_image(
                                token, group_openid, card, card.name, msg_id=message_id, msg_seq=2
                            )
                            module.send_group_message(
                                token, group_openid, f"使用说明：{HELP_URL}", msg_id=message_id, msg_seq=3
                            )
                            log(f"luna: capability -> card+link group={group_openid}")
                        except Exception as exc:
                            log(f"luna capability failed: {exc}")
                        continue

                    image_urls = []
                    if has_image:
                        image_urls = (
                            jelly.image_urls(data.get("attachments"))[:2]
                            or jelly.deep_image_urls(data)[:2]
                        )
                        if not image_urls:
                            try:
                                module.send_group_message(
                                    token, group_openid, "……图没收到。重发一次。", msg_id=message_id
                                )
                            except Exception as exc:
                                log(f"luna image hint failed: {exc}")
                            continue

                    if image_urls and not args:
                        args = "看看这张图"

                    if not args:
                        try:
                            module.send_group_message(
                                token, group_openid, "用法：/露娜 想说的话", msg_id=message_id
                            )
                        except Exception as exc:
                            log(f"luna usage hint failed: {exc}")
                        continue
                    last_luna[cooldown_key] = now
                    if not is_sister_line and luna.is_sexual_violence(args):
                        log(f"luna: sexual violence key={cooldown_key} args={args[:40]!r}")
                        punish(
                            module,
                            token,
                            group_openid,
                            user_id,
                            message_id,
                            penalties,
                            random.choice(luna.SEXUAL_VIOLENCE_LINES),
                            minutes=10,
                        )
                        continue
                    if target_id and not is_sister_line and luna.is_offensive(args):
                        log(f"luna: offensive request key={cooldown_key} args={args[:30]}")
                        punish(
                            module,
                            token,
                            group_openid,
                            user_id,
                            message_id,
                            penalties,
                            "……你想让我替你咬人？这种私怨自己解决。",
                        )
                        continue
                    if target_id and luna.is_violence(args):
                        hits = [item for item in violence_hits.get(cooldown_key, []) if now - item < TASK_WINDOW]
                        hits.append(now)
                        violence_hits[cooldown_key] = hits
                        log(f"luna: violence proposal key={cooldown_key} count={len(hits)}")
                        if len(hits) >= TASK_LIMIT:
                            violence_hits[cooldown_key] = []
                            punish(
                                module,
                                token,
                                group_openid,
                                user_id,
                                message_id,
                                penalties,
                                "屡教不改。惩罚在此降临 —— 一小时后再开口吧。",
                            )
                        else:
                            try:
                                module.send_group_message(
                                    token,
                                    group_openid,
                                    random.choice(luna.VIOLENCE_LINES),
                                    msg_id=message_id,
                                )
                            except Exception as exc:
                                log(f"luna violence reply failed: {exc}")
                        continue

                    if not is_privileged and luna.is_probe(args):
                        log(f"luna: probe key={cooldown_key}")
                        punish(
                            module,
                            token,
                            group_openid,
                            user_id,
                            message_id,
                            penalties,
                            "……无聊。一小时后再来。",
                        )
                        continue

                    if not is_sister_line:
                        norm = re.sub(r"\s+", "", args)[:50]
                        bucket = [(text, ts) for text, ts in recent_texts.get(cooldown_key, []) if now - ts < REPEAT_WINDOW]
                        bucket.append((norm, now))
                        recent_texts[cooldown_key] = bucket
                        if sum(1 for text, _ in bucket if text == norm) >= REPEAT_LIMIT:
                            recent_texts[cooldown_key] = []
                            log(f"luna: repeat spam key={cooldown_key}")
                            punish(
                                module,
                                token,
                                group_openid,
                                user_id,
                                message_id,
                                penalties,
                                "同样的话说三遍。你们人类真闲。",
                            )
                            continue

                    if not is_privileged and luna.is_task(args):
                        hits = [item for item in task_hits.get(cooldown_key, []) if now - item < TASK_WINDOW]
                        hits.append(now)
                        task_hits[cooldown_key] = hits
                        log(f"luna: task violation key={cooldown_key} count={len(hits)}")
                        if len(hits) >= TASK_LIMIT:
                            task_hits[cooldown_key] = []
                            punish(
                                module,
                                token,
                                group_openid,
                                user_id,
                                message_id,
                                penalties,
                                "屡教不改。惩罚在此降临 —— 一小时后再开口吧。",
                            )
                            continue
                    try:
                        respect = (
                            (bool(target_id) and target_id in owners)
                            or (bool(user_id) and user_id in owners)
                            or (bool(target_name) and luna.is_respected(target_name))
                        )
                        if user_id and str(author.get("member_role") or "").lower() == "owner" and user_id not in owners:
                            owners.add(user_id)
                            save_owners(owners)
                        speaker_profile = (
                            profiles.summary(user_id)
                            if control_enabled("profile", True) and not role_card
                            else ""
                        )
                        if (
                            target_id
                            and target_id != user_id
                            and control_enabled("profile", True)
                            and not target_role
                        ):
                            target_profile = profiles.summary(target_id)
                            if target_profile:
                                speaker_profile = f"{speaker_profile}\n被 @ 的人：{target_profile}".strip()
                        reply_speaker = "姐姐" if is_sister_line else (role_name or speaker)
                        target_display = (
                            "另一位姐姐"
                            if roles.is_sister(target_role)
                            else (roles.display(target_role) or target_name)
                        ) if target_role else target_name
                        payload = dict(
                            module=module,
                            token=token,
                            luna=luna,
                            proactive=proactive,
                            group=group_openid,
                            cooldown_key=cooldown_key,
                            args=args,
                            context=context,
                            target=target_display,
                            sister=is_sister_line,
                            respect=respect,
                            images=[],
                            image_urls=image_urls,
                            profile=speaker_profile,
                            tools=tools,
                            tools_enabled=control_enabled("tools", True),
                            tool_message=tool_message,
                            speaker=reply_speaker,
                            speaker_label=speaker,
                            role_card=roles.card_text(role_card),
                            privileged=is_privileged,
                            role_name=role_name,
                            target_card=roles.target_card_text(target_role),
                            target_is_sister=roles.is_sister(target_role),
                            message_id=message_id,
                            user_id=user_id,
                            tier=tier,
                        )
                        submit_reply(
                            group_openid, "command", functools.partial(send_command_reply, **payload)
                        )
                        continue
                    except Exception as exc:
                        log(f"luna failed: {exc}")
                        try:
                            module.send_group_message(
                                token, group_openid, "……现在不想说话。", msg_id=message_id
                            )
                        except Exception as send_exc:
                            log(f"luna fallback failed: {send_exc}")
                    continue

                if cmd != "找图":
                    now = time.time()
                    if now - last_unknown.get(cooldown_key, 0.0) >= 60:
                        last_unknown[cooldown_key] = now
                        try:
                            module.send_group_message(
                                token,
                                group_openid,
                                "未知指令。发 /帮助 查看可用指令。",
                                msg_id=message_id,
                            )
                        except Exception as exc:
                            log(f"unknown command notice failed: {exc}")
                    log(f"unknown command={cmd}")
                    continue

                if not control_enabled("findImage", True):
                    log(f"findImage: disabled group={group_openid}")
                    continue

                request = parse_image_request(args)
                if not request:
                    try:
                        module.send_group_message(
                            token,
                            group_openid,
                            "用法：/找图 角色名 [1|2]，例如 /找图 灵梦 2",
                            msg_id=message_id,
                        )
                    except Exception as exc:
                        log(f"image usage hint failed: {exc}")
                    continue
                name, count = request
                if len(name) > 40:
                    continue

                now = time.time()
                remain = IMAGE_COOLDOWN - (now - last_image_reply.get(cooldown_key, 0.0))
                if remain > 0:
                    log(f"image: cooldown key={cooldown_key} remain={int(remain)}s name={name}")
                    if now - last_notice.get(cooldown_key, 0.0) >= 60:
                        last_notice[cooldown_key] = now
                        try:
                            module.send_group_message(
                                token,
                                group_openid,
                                f"⏳ 发图冷却中，{int(remain) + 1} 秒后再试。",
                                msg_id=message_id,
                            )
                        except Exception as exc:
                            log(f"image: cooldown notice failed: {exc}")
                    continue
                if cooldown_key in inflight:
                    log(f"image: busy key={cooldown_key}")
                    continue
                inflight.add(cooldown_key)
                progress = Progress(module, token, group_openid, message_id).start()
                try:
                    if count < 1 or count > MAX_IMAGES:
                        progress.say(f"一次最多发 {MAX_IMAGES} 张，n 只能是 1 或者 2。")
                        log(f"image: count hint sent name={name} count={count}")
                        continue

                    try:
                        if name in getattr(images, "RANDOM_WORDS", ()):
                            paths, tags = images.fetch_random(count)
                        else:
                            paths, tags = images.fetch_images(name, count)
                    except images.ImageNotFound as exc:
                        log(f"image: miss name={name} detail={exc}")
                        if now - last_hint.get(cooldown_key, 0) < HINT_COOLDOWN:
                            log(f"image: miss hint suppressed group={group_openid}")
                            continue
                        last_hint[cooldown_key] = now
                        progress.say(HINT_TEXT.format(name=name))
                        log(f"image: miss hint sent name={name}")
                        continue
                    except Exception as exc:
                        log(f"image: fetch failed name={name}: {exc}")
                        continue

                    delivered = 0
                    for index, path in enumerate(paths):
                        for attempt in range(2):
                            try:
                                seq = progress.next_seq()
                                result = module.send_group_image(
                                    token,
                                    group_openid,
                                    path,
                                    path.name,
                                    msg_id=message_id,
                                    msg_seq=seq,
                                )
                                delivered += 1
                                log(
                                    f"image sent {path.name} tags={tags} seq={seq} "
                                    f"id={result.get('id')}"
                                )
                                break
                            except Exception as exc:
                                log(f"image send failed {path.name} try={attempt + 1}: {exc}")
                                time.sleep(1.0)
                        if index + 1 < len(paths):
                            time.sleep(0.6)
                    if delivered:
                        last_image_reply[cooldown_key] = time.time()
                        log(f"image: cooldown starts key={cooldown_key} {IMAGE_COOLDOWN}s")
                    else:
                        try:
                            module.send_group_message(
                                token,
                                group_openid,
                                "……图没发出去，再发一次。",
                                msg_id=message_id,
                                msg_seq=progress.next_seq(),
                            )
                        except Exception as exc:
                            log(f"image: fail notice failed: {exc}")
                finally:
                    progress.finish()
                    inflight.discard(cooldown_key)
        except Exception as exc:
            log(f"connection error: {exc}")
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
        time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
