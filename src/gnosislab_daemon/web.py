"""Localhost management API + console. Authentication:

- API: ``Authorization: Bearer <management-token>``
- Browser: POST /api/login with the token → HttpOnly SameSite=Strict
  session cookie; mutations then also require X-CSRF-Token matching
  the session. Origin/Host must be loopback; POSTs must be JSON;
  bodies are size-bounded.

No TLS, no external binds, no arbitrary file serving, no shell.
"""

from __future__ import annotations

import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

MAX_BODY = 65536
STATIC = Path(__file__).parent / "static"


def make_server(daemon, listen: str, port: int) -> ThreadingHTTPServer:
    if listen not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(
            f"daemon console refuses non-loopback bind: {listen}")
    sessions: dict[str, dict] = {}

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "gnosislab-daemon/0.1"

        # -- plumbing ---------------------------------------------------
        def log_message(self, fmt, *args):
            return  # daemon keeps its own event log

        def _send(self, code: int, body=b"", ctype="application/json",
                  headers: dict | None = None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("body too large")
            if n == 0:
                return {}
            ct = self.headers.get("Content-Type", "")
            if "application/json" not in ct:
                raise ValueError("Content-Type must be application/json")
            return json.loads(self.rfile.read(n))

        # -- auth ---------------------------------------------------------
        def _token(self) -> str | None:
            return daemon.root.read_token()

        def _authed(self) -> str | None:
            """Returns 'bearer' | 'cookie' | None."""
            auth = self.headers.get("Authorization", "")
            if auth == f"Bearer {self._token()}":
                return "bearer"
            cookie = self.headers.get("Cookie", "")
            for part in cookie.split(";"):
                k, _, v = part.strip().partition("=")
                if k == "gd_session" and v in sessions:
                    return "cookie"
            return None

        def _csrf_ok(self) -> bool:
            cookie = self.headers.get("Cookie", "")
            sess = None
            for part in cookie.split(";"):
                k, _, v = part.strip().partition("=")
                if k == "gd_session":
                    sess = sessions.get(v)
            if not sess:
                return False
            return self.headers.get("X-CSRF-Token") == sess["csrf"]

        def _loopback_headers(self) -> bool:
            host = self.headers.get("Host", "")
            if not (host.startswith("127.0.0.1")
                    or host.startswith("localhost")
                    or host.startswith("[::1]")):
                return False
            origin = self.headers.get("Origin")
            if origin is not None:
                return origin.startswith(("http://127.0.0.1",
                                          "http://localhost",
                                          "http://[::1]"))
            return True

        def _session_csrf(self) -> str | None:
            cookie = self.headers.get("Cookie", "")
            for part in cookie.split(";"):
                k, _, v = part.strip().partition("=")
                if k == "gd_session" and v in sessions:
                    return sessions[v]["csrf"]
            return None

        # -- routing --------------------------------------------------------
        def _route(self):
            u = urlparse(self.path)
            parts = [p for p in u.path.split("/") if p]
            return u.path, parts

        def do_GET(self):
            path, parts = self._route()
            if path == "/" or path == "/index.html":
                if self._authed() != "cookie":
                    return self._login_page()
                return self._console()
            if not self._loopback_headers():
                return self._send(403, {"error": "bad host/origin"})
            if path == "/api/health":
                return self._send(200, {
                    "ok": True, "gen": daemon.gen,
                    "uptime_s": int(time.time() - daemon.started),
                    "loops": len(daemon.loops)})
            if self._authed() is None:
                return self._send(401, {"error": "auth required"})
            if path == "/api/session":
                return self._send(200, {"csrf": self._session_csrf()})
            if path == "/api/agents":
                return self._send(200, {
                    "agents": sorted(daemon.config.agents)})
            if path == "/api/loops":
                return self._send(200, {
                    "loops": [r.loop.as_dict()
                              for r in daemon.loops.values()]})
            if parts[:2] == ["api", "loops"] and len(parts) == 3:
                r = daemon.loops.get(parts[2])
                if not r:
                    return self._send(404, {"error": "no such loop"})
                d = r.loop.as_dict()
                d["events"] = daemon.root.read_events(r.loop.id)
                return self._send(200, d)
            if path == "/api/services":
                try:
                    return self._send(200, {"services": daemon.services()})
                except Exception as exc:
                    return self._send(409, {"error": str(exc)})
            if parts[:2] == ["api", "services"] and len(parts) == 4 \
                    and parts[3] == "logs":
                try:
                    return self._send(200, {
                        "log": daemon.service_log_tail(parts[2])})
                except KeyError:
                    return self._send(404, {"error": "unknown service"})
            if path == "/api/export/jobs":
                return self._send(200, {
                    "jobs": list(daemon.export_jobs.values())})
            if parts[:3] == ["api", "export", "artifacts"] \
                    and len(parts) > 3:
                return self._artifact("/".join(parts[3:]))
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            path, parts = self._route()
            if not self._loopback_headers():
                return self._send(403, {"error": "bad host/origin"})
            try:
                body = self._body()
            except (ValueError, json.JSONDecodeError) as exc:
                return self._send(400, {"error": str(exc)})
            if path == "/api/login":
                if body.get("token") != self._token():
                    return self._send(403, {"error": "bad token"})
                sid, csrf = (secrets.token_urlsafe(24),
                             secrets.token_urlsafe(24))
                sessions[sid] = {"csrf": csrf, "created": time.time()}
                return self._send(200, {"csrf": csrf}, headers={
                    "Set-Cookie":
                        f"gd_session={sid}; Path=/; HttpOnly; "
                        "SameSite=Strict"})
            if self._authed() is None:
                return self._send(401, {"error": "auth required"})
            if self._authed() == "cookie" and not self._csrf_ok():
                return self._send(403, {"error": "bad csrf token"})
            if path == "/api/loops":
                return self._create_loop(body)
            if parts[:2] == ["api", "loops"] and len(parts) == 4:
                return self._loop_op(parts[2], parts[3])
            if parts[:2] == ["api", "services"] and len(parts) == 4:
                return self._service_op(parts[2], parts[3])
            if path == "/api/export":
                args = body.get("args", [])
                if not isinstance(args, list) \
                        or not all(isinstance(a, str) for a in args):
                    return self._send(400, {"error": "args must be argv"})
                return self._send(202, {
                    "job": daemon.run_export(args)})
            return self._send(404, {"error": "not found"})

        # -- handlers -------------------------------------------------------
        def _login_page(self):
            page = (STATIC / "login.html").read_text() \
                if (STATIC / "login.html").is_file() else \
                "<form method=post action=/api/login>" \
                "<input name=token type=password autofocus>" \
                "<button>Sign in</button></form>"
            page = (page
                    .replace("__TOKEN_PATH__",
                             str(daemon.root.token_path))
                    .replace("__STATE_DIR__",
                             str(daemon.root.root)))
            # login form posts JSON via fetch — served as text/html
            self._send(200, page.encode(), ctype="text/html")

        def _console(self):
            self._send(200, (STATIC / "index.html").read_bytes(),
                     ctype="text/html")

        def _create_loop(self, b: dict):
            check = b.get("check", [])
            if not isinstance(check, list) or not check \
                    or not all(isinstance(a, str) for a in check):
                return self._send(400, {"error": "check must be argv"})
            try:
                loop = daemon.create_loop(
                    agent=str(b.get("agent", "")),
                    task_file=str(b.get("task", "")),
                    workdir=str(b.get("workdir", ".")),
                    checker_argv=check,
                    limits=b.get("limits") or {},
                    watch=bool(b.get("watch")),
                    autostart=bool(b.get("autostart", True)))
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            return self._send(201, {"id": loop.id, "status": loop.status})

        def _loop_op(self, loop_id: str, op: str):
            r = daemon.loops.get(loop_id)
            if not r:
                return self._send(404, {"error": "no such loop"})
            fn = {"pause": r.pause, "resume": r.resume, "stop": r.stop} \
                .get(op)
            if fn is None:
                return self._send(404, {"error": "unknown op"})
            fn()
            return self._send(200, {"id": loop_id,
                                    "status": r.loop.status})

        def _service_op(self, name: str, op: str):
            if op not in ("start", "stop", "restart"):
                return self._send(404, {"error": "unknown op"})
            try:
                return self._send(200, daemon.service_op(name, op))
            except KeyError:
                return self._send(404, {"error": "unknown service"})
            except Exception as exc:
                return self._send(409, {"error": str(exc)})

        def _artifact(self, rel: str):
            base = daemon.exports_dir().resolve()
            target = (base / rel).resolve()
            if not str(target).startswith(str(base) + "/") \
                    or not target.is_file():
                return self._send(404, {"error": "not found"})
            self._send(200, target.read_bytes(),
                     ctype="application/octet-stream")

    return ThreadingHTTPServer((listen, port), H)
