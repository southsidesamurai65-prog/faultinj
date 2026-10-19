"""Load environment variables from a ``.env`` file (no third-party deps).

Search order (first existing wins per key):
    1. ``$PIPE_ENV_FILE`` (explicit path)
    2. ``./.env``
    3. ``<project>/pipe/.env``

Existing environment variables are never overwritten unless ``override=True``.
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_NAME = ".env"


def _candidates(path: str | Path | None) -> list[Path]:
    if path is not None:
        return [Path(path)]
    out: list[Path] = []
    explicit = os.environ.get("PIPE_ENV_FILE")
    if explicit:
        out.append(Path(explicit))
    out.append(Path.cwd() / _ENV_NAME)
    out.append(Path(__file__).resolve().parent.parent / _ENV_NAME)
    return out


def parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def load_env(path: str | Path | None = None, override: bool = False) -> dict[str, str]:
    """Load ``.env`` into ``os.environ``; return the merged values."""
    merged: dict[str, str] = {}
    for candidate in _candidates(path):
        if not candidate.is_file():
            continue
        values = parse_env_text(candidate.read_text())
        for key, value in values.items():
            if override or key not in os.environ:
                os.environ[key] = value
            merged[key] = value
    return merged
