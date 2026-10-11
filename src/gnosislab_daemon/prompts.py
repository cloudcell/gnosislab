"""Prompt assembly: task + bounded checker feedback + context.

The agent sees the *measurement* (the checker's own output), never the
daemon's opinion of it — feedback is checker stdout/stderr, truncated.
"""

from __future__ import annotations


def initial_prompt(task_text: str) -> str:
    return task_text


def continue_prompt(task_text: str, check_tail: str,
                    max_bytes: int) -> str:
    tail = check_tail.encode("utf-8", "replace")[-max_bytes:] \
        .decode("utf-8", "replace")
    return (
        f"{task_text}\n\n"
        "---\n"
        "The previous attempt did not pass the independent "
        "verification. This is the checker's output — fix the actual "
        "work it measured, not the message:\n\n"
        f"{tail}"
    )


def restart_prompt(task_text: str, check_tail: str, context_note: str,
                   max_bytes: int) -> str:
    base = continue_prompt(task_text, check_tail, max_bytes)
    if context_note:
        base += f"\n\nContext from prior attempts:\n{context_note}"
    return base
