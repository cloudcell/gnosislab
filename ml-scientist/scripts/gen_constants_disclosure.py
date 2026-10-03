"""Regenerate ``constants/disclosure.md`` from the grounded-constants
registry.

The table is checked in and pinned by test: run without arguments to
rewrite it after editing ``constants/grounded_constants.py`` or any
``src/`` call site; ``--check`` fails without writing when the
checked-in copy is stale.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / "constants" / "grounded_constants.py"
DISCLOSURE = ROOT / "constants" / "disclosure.md"
SRC = ROOT / "src"


def _load_registry():
    """Import the canonical registry module by path."""
    spec = importlib.util.spec_from_file_location(
        "grounded_constants", CANONICAL
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _call_sites(names: set[str]) -> dict[str, list[str]]:
    """``_gc.NAME`` references under src/, excluding the mirrors."""
    sites = {n: [] for n in names}
    pats = {n: re.compile(rf"_gc\.{n}\b") for n in names}
    for py in sorted(SRC.rglob("*.py")):
        if py.name == "_grounded_constants.py":
            continue
        rel = py.relative_to(ROOT).as_posix()
        for i, line in enumerate(
            py.read_text(encoding="utf-8").splitlines(), 1
        ):
            for name, pat in pats.items():
                if pat.search(line):
                    sites[name].append(f"{rel}:{i}")
    return sites


def _last_changed(sites: list[str]) -> str:
    """Sorted short hashes of the last commit touching each call-site
    file — empty (no call sites, or no git history) renders ``—``."""
    hashes = set()
    for path in {s.rsplit(":", 1)[0] for s in sites}:
        r = subprocess.run(
            ["git", "log", "-1", "--format=%h", "--", path],
            cwd=ROOT, capture_output=True, text=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            hashes.add(r.stdout.strip())
    return ", ".join(sorted(hashes)) if hashes else "—"


def _render(reg) -> str:
    sites = _call_sites(set(reg.REGISTRY))
    lines = [
        "# Constants disclosure — generated",
        "",
        "Regenerate: `uv run python scripts/gen_constants_disclosure.py`.",
        "Do not edit by hand — the checked-in copy is pinned by test.",
        "",
        "| Constant | Value / procedure | Class | Status | Scoring | Decision-load | Grounding | Call sites | Last-changed |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for c in reg.REGISTRY.values():
        vp = f"`{c.value!r}`" if c.value is not None else f"`{c.procedure}(…)`"
        if c.status == "OPERATIONAL" or not c.source_key:
            grounding = c.status
        else:
            s = reg.SOURCES[c.source_key]
            grounding = f"{c.status} — {s.filename or s.citation} ({c.locator})"
        site_list = sites[c.name]
        lines.append(
            f"| `{c.name}` | {vp} | {c.cls} | {c.status} "
            f"| {'yes' if c.scoring_path else 'no'} "
            f"| {'yes' if c.decision_load else 'no'} "
            f"| {grounding} "
            f"| {', '.join(site_list) if site_list else '—'} "
            f"| {_last_changed(site_list)} |"
        )
    lines += [
        "",
        "## Sources",
        "",
        "| Key | Citation | Corpus file | sha256 |",
        "| --- | --- | --- | --- |",
    ]
    for key, s in reg.SOURCES.items():
        lines.append(
            f"| `{key}` | {s.citation} "
            f"| {s.filename or '— (not archived)'} "
            f"| `{s.sha256[:12] if s.sha256 else '—'}` |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    text = _render(_load_registry())
    if "--check" in sys.argv[1:]:
        if DISCLOSURE.read_text(encoding="utf-8") != text:
            print(
                "constants/disclosure.md is stale — regenerate with "
                "scripts/gen_constants_disclosure.py",
                file=sys.stderr,
            )
            return 1
        return 0
    DISCLOSURE.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
