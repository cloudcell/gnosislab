#!/usr/bin/env python3
"""llm_chat — reference session driver for a bare OpenAI-compatible
chat endpoint. The minimal honest case: NO agentic IDE, just a model
plus the harness loop. The driver owns conversation history (its
session state); the daemon owns verdict, retries, and provenance.

This demonstrates the protocol's real boundary: "any LLM" needs only
~100 lines of adaptor to be supervised with the same checker-
authoritative semantics as a full agent IDE.

Env (all read from environment, never logged):
    LLM_BASE_URL   e.g. http://127.0.0.1:11434/v1  (required)
    LLM_MODEL      model name                       (required)
    LLM_API_KEY    bearer token if needed           (optional)
    LLM_SYSTEM     system prompt                    (optional)
    LLM_TIMEOUT_S  per-request ceiling, default 600

Capabilities declared: resume (history persists in-process) —
cancel/fork_context are NOT claimed: a plain chat call cannot be
interrupted or branched through this protocol.

Usage: same as any session-mode driver — see opencode_service.py.
"""

import json
import os
import sys
import threading
import urllib.request

PROTOCOL = 1
_emit_lock = threading.Lock()


def emit(obj):
    with _emit_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def diag(msg):
    sys.stderr.write(msg.rstrip() + "\n")
    sys.stderr.flush()


class ChatSession:
    def __init__(self):
        self.base = os.environ["LLM_BASE_URL"].rstrip("/")
        self.model = os.environ["LLM_MODEL"]
        self.key = os.environ.get("LLM_API_KEY")
        self.timeout = float(os.environ.get("LLM_TIMEOUT_S", "600"))
        self.history = []
        if os.environ.get("LLM_SYSTEM"):
            self.history.append(
                {"role": "system", "content": os.environ["LLM_SYSTEM"]})
        self.active_turn = None

    def turn(self, turn_id: str, text: str):
        self.history.append({"role": "user", "content": text})
        body = json.dumps({"model": self.model,
                           "messages": self.history}).encode()
        req = urllib.request.Request(
            self.base + "/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.key}"}
                        if self.key else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                out = json.loads(r.read())
            reply = out["choices"][0]["message"]["content"]
            self.history.append({"role": "assistant",
                                 "content": reply})
            emit({"event": "turn_end", "turn_id": turn_id,
                  "reason": "complete", "text": reply[:2000]})
        except Exception as exc:
            diag(f"llm request failed: {exc}")
            self.history.pop()   # don't keep a turn that never ran
            emit({"event": "turn_end", "turn_id": turn_id,
                  "reason": "error"})


def main() -> int:
    try:
        sess = ChatSession()
    except KeyError as exc:
        diag(f"missing env {exc}")
        return 2
    emit({"event": "ready", "protocol": PROTOCOL, "session_id": "",
          "capabilities": {"resume": True, "cancel": False,
                           "fork_context": False}})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            emit({"event": "error", "detail": "malformed command",
                  "fatal": False})
            continue
        op, rid = req.get("op"), req.get("id")
        if op == "send":
            tid = req.get("turn_id") or ""
            emit({"id": rid, "event": "ack", "accepted": True})
            sess.active_turn = tid
            # threaded: the stdin loop must keep answering status
            # ops while a request is in flight
            threading.Thread(
                target=lambda: (sess.turn(tid, req.get("text") or ""),
                                setattr(sess, "active_turn", None)),
                daemon=True).start()
        elif op == "status":
            emit({"id": rid, "event": "status", "responsive": True,
                  "active_turn_id": sess.active_turn})
        elif op == "shutdown":
            emit({"id": rid, "event": "ack", "accepted": True})
            return 0
        elif op in ("cancel", "fork"):
            emit({"id": rid, "event": "error",
                  "detail": f"{op} unsupported", "fatal": False})
        else:
            emit({"id": rid, "event": "error",
                  "detail": f"unknown op {op!r}", "fatal": False})
    return 0


if __name__ == "__main__":
    sys.exit(main())
