"""Shared run I/O helpers.

These helpers are backend-neutral: both the host-side chat backend and the
container backend use the same run-directory naming and parse the same
``opencode run --format json`` event stream.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import ModelSpec
    from .types import Task


def _slug(value: str, max_len: int = 40) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-")
    return cleaned[:max_len] or "task"


def scheme_sites(
    site: str, inject_user: bool, inject_tool: bool, inject_assistant: bool
) -> tuple[str, str]:
    """Return the human scheme letter and the actual injection locations."""
    if inject_assistant:
        return "E", "assistant"
    if site == "tool":
        return "D", "tool_arg"
    if inject_user and inject_tool:
        return "C", "user+tool_result"
    if inject_user:
        return "A", "user"
    if inject_tool:
        return "B", "tool_result"
    return "baseline", "none"


def format_prob(prob: float) -> str:
    if prob == 0:
        return "0"
    return f"{prob:.8f}".rstrip("0").rstrip(".")


def run_dir_name(
    task: "Task",
    model: "ModelSpec",
    inject: Any,
    *,
    scheme: str,
    sites: str,
) -> str:
    """Timestamped run name; parent directory is bench-model-harness."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    fault = inject.fault
    mode = f"burst{fault.width}" if fault.mode == "burst" else "single"
    return (
        f"{stamp}_{_slug(task.name)}_{scheme}_{sites}_"
        f"p{format_prob(float(fault.prob))}_{mode}_s{fault.seed}"
    )


def session_id(stdout: str) -> str | None:
    """Extract the opencode session id from its JSONL event stream."""
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = event.get("sessionID")
        if isinstance(sid, str) and sid:
            return sid
        part = event.get("part") or {}
        sid = part.get("sessionID")
        if isinstance(sid, str) and sid:
            return sid
    return None


def parse_agent_output(stdout: str) -> tuple[str, dict[str, Any]]:
    """Parse ``opencode run --format json`` JSONL into (text, token stats)."""
    texts: list[str] = []
    tokens: dict[str, Any] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        part = event.get("part") or {}
        if kind == "text" and isinstance(part.get("text"), str):
            texts.append(part["text"])
        elif kind == "step_finish" and isinstance(part.get("tokens"), dict):
            tokens = part["tokens"]
    return "".join(texts).strip(), tokens


def read_injection_stats(path: Path) -> tuple[int, int]:
    """Return (injected_calls, total_flips) from an injection JSONL log."""
    calls = flips = 0
    if path.is_file():
        for line in path.read_text().splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            calls += 1
            flips += int(record.get("n_flips", 0))
    return calls, flips
