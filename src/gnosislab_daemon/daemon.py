"""Daemon: process owner, supervisor tick, lifecycle, concurrency,
recovery. One daemon per state root; writes daemon.json before the
HTTP listener binds so --state-dir resolution never sees a half-up
manager.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import signal
import sys
import threading
import time
from pathlib import Path

from .config import DaemonConfig, load
from .loop import LoopRunner, task_hash
from .state import StateRoot, LoopState, utcnow

GENERATION = 1


class Daemon:
    def __init__(self, state_root: Path, config: DaemonConfig):
        self.root = StateRoot(state_root)
        self.config = config
        self.loops: dict[str, LoopRunner] = {}
        self.stop_flag = False
        self.started = time.time()
        self.gen = f"{int(self.started)}-{os.getpid()}"
        self.lock = threading.Lock()
        self.export_jobs: dict[str, dict] = {}

    # -- loops ------------------------------------------------------------
    def _runner(self, loop: LoopState) -> LoopRunner | None:
        profile = self.config.agents.get(loop.agent)
        if profile is None:
            return None
        limits = dict(self.config.limits)
        limits.update(loop.limits or {})
        return LoopRunner(loop, self.root, profile, limits)

    def discover(self) -> None:
        """Recover persisted loops after a restart; reconcile children
        before any new launch."""
        for ls in self.root.list_loops():
            r = self._runner(ls)
            if r is None:
                continue
            self.loops[ls.id] = r
            r.adopt()

    def create_loop(self, agent: str, task_file: str, workdir: str,
                    checker_argv: list[str], limits: dict | None = None,
                    watch: bool = False, autostart: bool = True) -> LoopState:
        if agent not in self.config.agents:
            raise ValueError(f"unknown agent profile '{agent}'")
        tf, wd = Path(task_file), Path(workdir)
        if not tf.is_file():
            raise ValueError(f"task file not found: {task_file}")
        if not wd.is_dir():
            raise ValueError(f"workdir not found: {workdir}")
        loop = LoopState(
            id=self.root.new_loop_id(), task_file=str(tf.resolve()),
            task_hash=task_hash(tf), workdir=str(wd.resolve()),
            agent=agent, checker_argv=list(checker_argv),
            watch=watch, limits=dict(limits or {}),
            created=utcnow(), cycle_started=utcnow(),
            status="created" if autostart else "paused",
        )
        self.root.save_loop(loop)
        self.root.emit(loop.id, "create", agent=agent)
        with self.lock:
            r = self._runner(loop)
            if r is not None:
                self.loops[loop.id] = r
        return loop

    # -- supervisor tick ---------------------------------------------------
    def _active_agents(self) -> int:
        return sum(
            1 for r in self.loops.values()
            if r.loop.status == "running")

    def tick(self) -> None:
        now = time.time()
        with self.lock:
            runners = list(self.loops.values())
        for r in runners:
            if r.loop.status == "created" \
                    and self._active_agents() >= self.config.max_concurrent:
                continue  # pending: slot not free
            r.tick(now)

    def supervise(self, interval: float = 0.5) -> None:
        while not self.stop_flag:
            self.tick()
            time.sleep(interval)
        self._shutdown_children()

    def _shutdown_children(self) -> None:
        for r in self.loops.values():
            if r.loop.status in ("running", "verifying"):
                r.stop()

    def shutdown(self) -> None:
        self.stop_flag = True

    # -- service control (same code path as `gnosislab` CLI) ---------------
    @staticmethod
    def services() -> list[dict]:
        from gnosislab import cli as gcli
        out = []
        for name in gcli.COMPONENTS:
            out.append({
                "name": name,
                "up": gcli._is_up(name),
                "managed_pid": gcli._managed_pid(name),
                "mcp_port": int(gcli._var(name, "PORT")),
                "gui_port": int(gcli._var(name, "GUI_PORT")),
                "available": True,
            })
        return out

    @staticmethod
    def service_op(name: str, op: str) -> dict:
        from gnosislab import cli as gcli
        if name not in gcli.COMPONENTS:
            raise KeyError(name)
        fn = {"start": gcli.start_one, "stop": gcli.stop_one}[op]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            if op == "restart":
                gcli.stop_one(name)
                # stop_one returns once the pid dies, but the socket can
                # still be held (TIME_WAIT/lingering children) — an
                # immediate rebind fails with "port in use" and the new
                # server exits. Wait for the port to actually release.
                ports = [int(gcli._var(name, "PORT")),
                         int(gcli._var(name, "GUI_PORT"))]
                deadline = time.time() + 15
                while any(gcli._port_pid(p) for p in ports) \
                        and time.time() < deadline:
                    time.sleep(0.25)
                rc = gcli.start_one(name)
            else:
                rc = fn(name)
        return {"rc": rc, "output": buf.getvalue()[-4000:]}

    @staticmethod
    def service_log_tail(name: str, n: int = 100) -> str:
        from gnosislab import cli as gcli
        if name not in gcli.COMPONENTS:
            raise KeyError(name)
        log = gcli._home(name) / "logs" / "server.log"
        try:
            return "".join(log.read_text().splitlines(
                keepends=True)[-n:])
        except OSError:
            return ""

    # -- export jobs -------------------------------------------------------
    def run_export(self, args: list[str]) -> str:
        job_id = hashlib.sha256(
            f"{time.time()}{args}".encode()).hexdigest()[:10]
        self.export_jobs[job_id] = {
            "id": job_id, "args": args, "status": "running",
            "rc": None, "output": "", "started": utcnow(),
        }
        threading.Thread(target=self._export_job, args=(job_id, args),
                         daemon=True).start()
        return job_id

    def _export_job(self, job_id: str, args: list[str]) -> None:
        job = self.export_jobs[job_id]
        self.exports_dir().mkdir(parents=True, exist_ok=True)
        cwd = os.getcwd()
        buf = io.StringIO()
        try:
            os.chdir(self.exports_dir())
            from gnosislab import cli as gcli
            with contextlib.redirect_stdout(buf):
                job["rc"] = gcli._export(list(args))
        except Exception as exc:  # export must never kill the daemon
            job["rc"] = 2
            buf.write(f"export error: {exc}\n")
        finally:
            os.chdir(cwd)
        job["status"] = "done"
        job["output"] = buf.getvalue()[-8000:]
        job["finished"] = utcnow()

    def exports_dir(self) -> Path:
        return self.root.exports_dir


def serve(state_root: Path, listen: str | None = None,
          port: int | None = None) -> int:
    root = StateRoot(state_root)
    if not root.acquire_lock():
        print(f"daemon: state root {state_root} already has a live "
              "daemon (daemon.lock held)", file=sys.stderr)
        return 1
    config = load(root.root)
    if listen:
        config.listen = listen
    if port is not None:
        config.port = port
    # [daemon.env] — scope this daemon's environment (and its children's)
    # before gnosislab.cli is ever imported: service/export ops resolve
    # ports and ~/.ml-* homes from os.environ lazily, so the override
    # here makes another stack unreachable rather than merely unused.
    # The state root owns its environment: these values WIN over the
    # inherited env — otherwise an always-set variable like HOME could
    # never be redirected and isolation would silently fail.
    for k, v in config.env.items():
        os.environ[k] = v
    d = Daemon(root.root, config)
    d.discover()

    from .web import make_server
    httpd = make_server(d, config.listen, config.port)
    actual_port = httpd.server_address[1]

    root.write_daemon_json({
        "pid": os.getpid(), "port": actual_port,
        "listen": config.listen, "generation": GENERATION,
        "gen": d.gen, "started": utcnow(),
        "state_root": str(root.root.resolve()),
    })
    root.ensure_token()

    def _sig(*_):
        d.shutdown()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    sup = threading.Thread(target=d.supervise, daemon=True)
    sup.start()
    print(f"daemon: state root {root.root} | console "
          f"http://{config.listen}:{actual_port}/ | pid {os.getpid()}")
    print(f"daemon: management token at {root.token_path} "
          f"(or 'gnosislab daemon token --state-dir {root.root}')")
    try:
        httpd.serve_forever()
    finally:
        d.shutdown()
        sup.join(timeout=10)
        httpd.server_close()
        root.release()
    return 0
