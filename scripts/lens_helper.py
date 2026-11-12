#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""常驻在交互会话里的识图服务：bot 在计划任务会话里连不上浏览器扩展，由这里代跑。"""

from __future__ import annotations

import json
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import google_lens

PORT = 7391
LOCK = threading.Lock()

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_DIR / "lens-helper.log"),
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    encoding="utf-8",
)
log = logging.getLogger("lens-helper")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
            path = Path(data.get("path") or "")
            if not path.is_file():
                raise FileNotFoundError(str(path))
            with LOCK:
                text = google_lens.identify(path, allow_helper=False)
            payload, code = {"text": text}, 200
            log.info("ok %s chars=%s", path.name, len(text))
        except Exception as exc:
            payload, code = {"error": str(exc)[:200]}, 500
            log.info("fail %s", exc)
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args) -> None:
        return


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    log.info("helper listening on %s", PORT)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
