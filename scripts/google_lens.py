#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Google Lens 识图：临时托管图片 + 通过 Playwright MCP 读取 Lens 结果。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = ROOT / "luna-site-deploy"
SITE_BASE = "https://luna-site-d0gj36nib486137a4-1524033896.tcloudbaseapp.com"
ENV_ID = "luna-site-d0gj36nib486137a4"
CONFIG = Path.home() / ".config" / "opencode" / "opencode.jsonc"
MCP_CMD = ["cmd", "/c", "npx", "-y", "@playwright/mcp@latest", "--extension"]


def log(message: str) -> None:
    print(f"[lens] {message}", flush=True)


def _token() -> str:
    try:
        text = CONFIG.read_text(encoding="utf-8")
        matched = re.search(r"PLAYWRIGHT_MCP_EXTENSION_TOKEN\"\s*:\s*\"([^\"]+)\"", text)
        return matched.group(1) if matched else ""
    except Exception:
        return ""


def _tcb() -> str:
    return shutil.which("tcb") or shutil.which("tcb.cmd") or ""


def publish(path: Path) -> tuple[str, str]:
    """把图片临时发到官网静态目录，返回 (公网 URL, 远端路径)。"""
    remote = f"assets/img/lens-{uuid.uuid4().hex[:10]}.jpg"
    local = SITE_DIR / remote
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(path.read_bytes())
    tcb = _tcb()
    if not tcb:
        raise RuntimeError("找不到 tcb 命令")
    result = subprocess.run(
        [tcb, "hosting", "deploy", str(local), remote, "-e", ENV_ID],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        raise RuntimeError(f"tcb deploy failed: {result.stderr[-120:]}")
    url = f"{SITE_BASE}/{remote}"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            response = requests.head(url, timeout=8)
            if response.status_code == 200:
                return url, remote
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("图片公网地址不可用")


def unpublish(remote: str) -> None:
    tcb = _tcb()
    if not tcb or not remote:
        return
    try:
        subprocess.run(
            [tcb, "hosting", "delete", remote, "-e", ENV_ID],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=40,
            cwd=str(ROOT),
        )
    except Exception:
        pass
    try:
        (SITE_DIR / remote).unlink(missing_ok=True)
    except Exception:
        pass


class _Mcp:
    def __init__(self) -> None:
        self.token = _token()
        if not self.token:
            raise RuntimeError("缺少 PLAYWRIGHT_MCP_EXTENSION_TOKEN")
        env = {**os.environ, "PLAYWRIGHT_MCP_EXTENSION_TOKEN": self.token}
        self.proc = subprocess.Popen(
            MCP_CMD,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
        )
        self.lines: list[str] = []
        self.lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()
        self._id = 0
        self.call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "luna-bot", "version": "1"},
        }, timeout=30)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def _read(self) -> None:
        for line in self.proc.stdout:
            line = line.strip()
            if line:
                with self.lock:
                    self.lines.append(line)

    def _send(self, payload: dict) -> None:
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def call(self, method: str, params: dict, timeout: int = 45) -> dict:
        self._id += 1
        req_id = self._id
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                for line in list(self.lines):
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    if data.get("id") == req_id:
                        self.lines.remove(line)
                        return data
            time.sleep(0.1)
        raise TimeoutError(method)

    def tool(self, name: str, arguments: dict, timeout: int = 45) -> str:
        data = self.call("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        content = (data.get("result") or {}).get("content") or []
        return "\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict))

    def close(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


def _extract(body: str) -> str:
    text = re.sub(r"[ \t]+", " ", body or "")
    if "异常流量" in text or "sorry/index" in text:
        raise RuntimeError("google captcha")
    start = text.find("AI 概览")
    if start < 0:
        raise RuntimeError("no ai overview")
    chunk = text[start + len("AI 概览"):].strip()
    for stop in ("外观匹配", "页脚链接", "AI 模式"):
        index = chunk.find(stop)
        if index > 0:
            chunk = chunk[:index]
    lines = [line.strip() for line in chunk.splitlines() if line.strip()]
    merged = " ".join(lines[:6])
    merged = re.split(r"\s(?:Fandom|Reddit|YouTube|Bilibili|Danbooru|Instagram|TapTap|Facebook|Pinterest|X|百度百科|库街区WIKI)\b", merged)[0]
    merged = re.sub(r"(?:\s\+\d+)+\s*$", "", merged).strip()
    return merged[:400]


def identify(path: Path) -> str:
    """返回 Lens 识别到的一句话；失败抛异常。"""
    url, remote = publish(path)
    try:
        mcp = _Mcp()
        try:
            proxied = "https://images.weserv.nl/?url=" + quote(url.split("://", 1)[1], safe="")
            target = (
                "https://www.google.com/searchbyimage?image_url="
                + quote(proxied, safe="")
                + "&hl=zh-CN"
            )
            mcp.tool("browser_navigate", {"url": target}, timeout=60)
            time.sleep(10)
            body = mcp.tool(
                "browser_evaluate",
                {"function": "() => document.body.innerText"},
                timeout=30,
            )
            text = body
            if "### Result" in text:
                text = text.split("### Result", 1)[1]
            text = text.strip()
            if text.startswith('"') and text.endswith('"'):
                text = text[1:-1]
            result = _extract(text.replace("\\n", "\n"))
            if result:
                return result
            raise RuntimeError("empty lens result")
        finally:
            mcp.close()
    finally:
        unpublish(remote)
