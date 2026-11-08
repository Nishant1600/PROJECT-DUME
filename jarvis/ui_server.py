"""Serves the HUD and streams events to it. Pure stdlib (no Flask/websockets).

* GET  /            -> the HUD page (jarvis/hud/index.html and its assets)
* GET  /events      -> Server-Sent Events stream of bus events (one browser or many)
* POST /input       -> {text}           typed command from the HUD
* POST /confirm     -> {id, allow}      answer to a confirmation
* POST /control     -> {action: stop|new_session|mute|unmute}
Inbound posts are handed to callbacks the orchestrator registers.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .bus import Bus

log = logging.getLogger(__name__)
HUD_DIR = Path(__file__).resolve().parent / "hud"
_MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json",
         ".svg": "image/svg+xml", ".ico": "image/x-icon", ".png": "image/png",
         ".woff2": "font/woff2", ".map": "application/json"}


class UIServer:
    def __init__(self, bus: Bus, host: str = "127.0.0.1", port: int = 0):
        self.bus = bus
        self.on_input = None     # callable(text)
        self.on_confirm = None   # callable(id, allow)
        self.on_control = None   # callable(action)
        handler = partial(_Handler, self)
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.host = host

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def start(self) -> None:
        threading.Thread(target=self.httpd.serve_forever, name="jarvis-ui", daemon=True).start()
        log.info("HUD server at %s", self.url)

    def stop(self) -> None:
        self.httpd.shutdown()


def _safe_asset(rel: str) -> Path | None:
    rel = rel.lstrip("/") or "index.html"
    target = (HUD_DIR / rel).resolve()
    if HUD_DIR in target.parents and target.is_file():
        return target
    if target == (HUD_DIR / "index.html").resolve() and target.is_file():
        return target
    return None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, server: UIServer, *a, **kw):
        self.ui = server
        super().__init__(*a, **kw)

    def log_message(self, *a):  # keep the console clean
        pass

    # ---- GET: static files and the SSE stream
    def do_GET(self):
        if self.path.split("?")[0] == "/events":
            return self._events()
        asset = _safe_asset(self.path.split("?")[0])
        if asset is None:
            self.send_error(404)
            return
        body = asset.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _MIME.get(asset.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _events(self):
        q = self.ui.bus.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=10)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")  # keep the socket alive
                    self.wfile.flush()
                    continue
                payload = json.dumps({"ts": ev.ts, **ev.data})
                self.wfile.write(f"event: {ev.type}\ndata: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.ui.bus.unsubscribe(q)

    # ---- POST: input / confirm / control
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            data = {}
        path = self.path.split("?")[0]
        try:
            if path == "/input" and self.ui.on_input:
                self.ui.on_input(str(data.get("text", "")))
            elif path == "/confirm" and self.ui.on_confirm:
                self.ui.on_confirm(str(data.get("id", "")), bool(data.get("allow")))
            elif path == "/control" and self.ui.on_control:
                self.ui.on_control(str(data.get("action", "")))
            else:
                self.send_error(404)
                return
        except Exception as e:
            log.exception("UI POST %s failed", path)
            self._json({"ok": False, "error": str(e)})
            return
        self._json({"ok": True})

    def _json(self, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
