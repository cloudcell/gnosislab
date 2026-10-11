"""ExecBackend — CLI-subprocess transport for agent turns (and
checkers, which are the same shape: argv → stdout → exit code).

Encapsulates what was previously inline in LoopRunner: spawn with an
isolated process group + inherited-but-corrected environment,
non-blocking bounded output drain, and process-group-scoped kill.
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

from . import BackendCapabilities


class ExecBackend:
    def __init__(self, workdir: Path):
        self.workdir = workdir

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(persistent=False)

    # -- subprocess plumbing ------------------------------------------
    def spawn(self, argv: list[str]) -> subprocess.Popen:
        proc = subprocess.Popen(
            argv, cwd=self.workdir,
            stdin=subprocess.DEVNULL,   # interactive prompts get EOF —
            # never a silent block; EOF never grants a permission
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True,
            # PWD is inherited stale (the daemon's own cwd); agents that
            # resolve their project dir from $PWD — opencode does —
            # would write into the repo instead of the loop's workdir.
            env={**os.environ, "PYTHONUNBUFFERED": "1",
                 "PWD": str(self.workdir)},
        )
        os.set_blocking(proc.stdout.fileno(), False)
        return proc

    def drain(self, stream, buf: bytearray, bound: int) -> int:
        """Non-blocking read into ``buf`` trimmed to ~4×bound/2×bound.
        Returns bytes read so the caller can stamp activity times."""
        if stream is None:
            return 0
        total = 0
        while True:
            try:
                chunk = os.read(stream.fileno(), 8192)
            except (OSError, BlockingIOError):
                return total
            if not chunk:
                return total
            total += len(chunk)
            buf.extend(chunk)
            if len(buf) > bound * 4:
                del buf[:-bound * 2]

    def kill(self, proc: subprocess.Popen | None,
             grace: float = 5.0) -> None:
        """Cooperative first: SIGTERM to the process GROUP spawned
        with start_new_session (descendants die with it — we created
        the group, ownership is ours), SIGKILL after ``grace``."""
        if proc is None:
            return
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        else:
            try:
                proc.wait(timeout=1)   # reap the zombie, close fds
            except subprocess.TimeoutExpired:
                pass
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass
