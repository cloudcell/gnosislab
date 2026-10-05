"""Payload path helpers shared by every export format.

Keeping the layout in one place means the RO-Crate, PROV-JSON, and
bare JSON-LD emitters all describe the same relative paths:

    payload/code/<sha256>.<ext>
    payload/artifacts/<sha256>_<filename>
    payload/results/<trial-id>.json
"""

from __future__ import annotations


def _lang_ext(language: str) -> str:
    return {"python": ".py", "r": ".R", "julia": ".jl", "bash": ".sh"}.get(
        language, ".txt"
    )


def code_path(code_hash: str, language: str) -> str:
    return f"payload/code/{code_hash}{_lang_ext(language)}"


def artifact_path(content_hash: str, filename: str) -> str:
    safe = filename.replace("/", "_")
    return f"payload/artifacts/{content_hash}_{safe}"


def results_path(trial_id: str) -> str:
    return f"payload/results/{trial_id}.json"
