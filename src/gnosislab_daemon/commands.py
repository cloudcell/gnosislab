"""``gnosislab daemon …`` — CLI client for the resident daemon.

Management commands resolve the daemon from a state root's
``daemon.json`` (port + pid) and authenticate with the
``management-token`` file. If the daemon is not running they say so
instead of silently opening a second manager.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import urllib.request
import webbrowser
from pathlib import Path

from .state import StateRoot

DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "gnosislab" / "daemon"

USAGE = """\
usage: gnosislab daemon <cmd> [opts]

  serve     [--state-dir DIR] [--listen IP] [--port N]   run the daemon
  start     --agent NAME --task FILE --check CMD...
            [--workdir DIR] [--state-dir DIR] [--max-turns N]
            [--max-seconds N] [--stagnation N] [--watch]
            (--check consumes the rest of the line — put it last)
  list      [--json] [--state-dir DIR]
  status    [LOOP_ID] [--json] [--state-dir DIR]
  pause     LOOP_ID [--state-dir DIR]
  resume    LOOP_ID [--state-dir DIR]
  stop      LOOP_ID [--state-dir DIR]
  agents    [--state-dir DIR]
  open      [--state-dir DIR]      open the local console
  token     [--state-dir DIR]      print the management token
  shutdown  [--state-dir DIR]      stop the daemon (SIGTERM)
"""


def _state_dir(argv: list[str]) -> Path:
    if "--state-dir" in argv:
        i = argv.index("--state-dir")
        return Path(argv[i + 1])
    return DEFAULT_STATE_DIR


def _daemon_ref(state_dir: Path) -> dict:
    root = StateRoot(state_dir)
    meta = root.read_daemon_json()
    if not meta or not meta.get("pid"):
        raise RuntimeError(
            f"no daemon at {state_dir} — start one with "
            "'gnosislab daemon serve'")
    return {"meta": meta, "token": root.read_token()}


def _api(state_dir: Path, method: str, path: str,
         body: dict | None = None) -> tuple[int, dict]:
    ref = _daemon_ref(state_dir)
    port = ref["meta"].get("port")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {ref['token']}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.build_opener(
                urllib.request.ProxyHandler({})).open(req,
                                                      timeout=15) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        with e:  # error response still holds a socket — close it
            try:
                return e.code, json.loads(e.read() or b"{}")
            except json.JSONDecodeError:
                return e.code, {"error": str(e)}
    except OSError as e:
        raise RuntimeError(
            f"daemon unreachable at {state_dir} — {e}") from e


def _flag(argv: list[str], name: str, default=None):
    if name in argv:
        i = argv.index(name)
        return argv[i + 1] if i + 1 < len(argv) else True
    return default


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0
    cmd, rest = argv[0], argv[1:]
    state_dir = _state_dir(rest)

    try:
        if cmd == "serve":
            from .daemon import serve
            return serve(state_dir, listen=_flag(rest, "--listen"),
                         port=int(_flag(rest, "--port"))
                         if _flag(rest, "--port") else None)

        if cmd == "token":
            root = StateRoot(state_dir)
            tok = root.read_token()
            if not tok:
                print(f"no token at {state_dir}", file=sys.stderr)
                return 1
            print(tok)
            return 0

        if cmd == "open":
            ref = _daemon_ref(state_dir)
            url = f"http://127.0.0.1:{ref['meta']['port']}/"
            print(f"console: {url}")
            if ref["token"]:
                print(f"token:   {ref['token']}   (paste at sign-in)")
            webbrowser.open(url)
            return 0

        if cmd == "shutdown":
            ref = _daemon_ref(state_dir)
            os.kill(int(ref["meta"]["pid"]), signal.SIGTERM)
            print("shutdown requested")
            return 0

        if cmd == "agents":
            code, out = _api(state_dir, "GET", "/api/agents")
            for a in out.get("agents", []):
                print(a)
            return 0 if code == 200 else 1

        if cmd == "list":
            code, out = _api(state_dir, "GET", "/api/loops")
            if "--json" in rest:
                print(json.dumps(out, indent=2))
            else:
                for l in out.get("loops", []):
                    print(f"{l['id']}  {l['status']:12} {l['agent']:10} "
                          f"attempts={l['attempts']}  {l['task_file']}")
            return 0 if code == 200 else 1

        if cmd == "status":
            loop_id = next((a for a in rest if not a.startswith("-")
                            and a != str(state_dir)), None)
            if loop_id:
                code, out = _api(state_dir, "GET",
                                 f"/api/loops/{loop_id}")
            else:
                code, out = _api(state_dir, "GET", "/api/health")
            print(json.dumps(out, indent=2))
            return 0 if code == 200 else 1

        if cmd == "start":
            agent = _flag(rest, "--agent")
            task = _flag(rest, "--task")
            workdir = _flag(rest, "--workdir", ".")
            check = None
            if "--check" in rest:
                check = rest[rest.index("--check") + 1:]
            if not agent or not task or not check:
                print("start needs --agent, --task and --check "
                      "(check must be last)", file=sys.stderr)
                return 2
            limits = {}
            for flag, key in (("--max-turns", "max_turns"),
                              ("--max-seconds", "max_seconds"),
                              ("--stagnation", "stagnation")):
                v = _flag(rest, flag)
                if v is not None:
                    limits[key] = int(v)
            code, out = _api(state_dir, "POST", "/api/loops", {
                "agent": agent, "task": task, "workdir": workdir,
                "check": check, "limits": limits,
                "watch": "--watch" in rest})
            if code not in (200, 201):
                print(out.get("error", out), file=sys.stderr)
                return 1
            print(f"{out['id']} ({out['status']})")
            return 0

        if cmd in ("pause", "resume", "stop"):
            loop_id = next((a for a in rest if not a.startswith("-")
                            and a != str(state_dir)), None)
            if not loop_id:
                print(f"{cmd} needs a LOOP_ID", file=sys.stderr)
                return 2
            code, out = _api(
                state_dir, "POST", f"/api/loops/{loop_id}/{cmd}", {})
            if code != 200:
                print(out.get("error", out), file=sys.stderr)
                return 1
            print(f"{out['id']} → {out['status']}")
            return 0

        print(USAGE, file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"gnosislab daemon: {exc}", file=sys.stderr)
        return 1
