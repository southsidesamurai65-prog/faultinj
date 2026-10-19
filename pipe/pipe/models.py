"""Models and per-harness connection endpoints (2x2 matrix).

Each *harness* has exactly one aggregated endpoint (one base_url + one key)
that serves all models. Models are just names; the harness pairs them with its
own connection. Keys/URLs come from environment variables only.

    CONNECTIONS["opencode"] -> opencode go port (one url + one key)
    CONNECTIONS["codex"]    -> codex endpoint    (one url + one key)

    2x2 = {opencode, codex} x {deepseek-v4.1-flash, gpt-6-luna}
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Connection:
    name: str
    base_url_env: str
    base_url_default: str | None
    api_key_env: str

    def resolve(self, env: dict[str, str] | None = None) -> dict[str, str]:
        src = env if env is not None else os.environ
        out: dict[str, str] = {}
        if src.get(self.api_key_env):
            out["api_key"] = src[self.api_key_env]
        url = src.get(self.base_url_env) or self.base_url_default
        if url:
            out["base_url"] = url
        return out


@dataclass(frozen=True)
class ModelSpec:
    name: str
    wire_model: str

    def id_for(self, provider: str) -> str:
        """opencode-style provider-qualified id (e.g. ``pipe/deepseek-v4.1-flash``)."""
        return f"{provider}/{self.wire_model}"


CONNECTIONS: dict[str, Connection] = {
    "opencode": Connection(
        name="opencode",
        base_url_env="PIPE_OPENCODE_BASE_URL",
        base_url_default="https://REPLACE_ME/opencode",
        api_key_env="PIPE_OPENCODE_API_KEY",
    ),
    "codex": Connection(
        name="codex",
        base_url_env="PIPE_CODEX_BASE_URL",
        base_url_default="https://xx.com/api",
        api_key_env="PIPE_CODEX_API_KEY",
    ),
}

MODELS: dict[str, ModelSpec] = {
    "deepseek-v4.1-flash": ModelSpec("deepseek-v4.1-flash", "deepseek-v4.1-flash"),
    "gpt-6-luna": ModelSpec("gpt-6-luna", "gpt-6-luna"),
}


def resolve_model(name: str) -> ModelSpec:
    try:
        return MODELS[name]
    except KeyError as exc:
        raise KeyError(f"unknown model {name!r}; known: {sorted(MODELS)}") from exc


def resolve_connection(
    harness: str, env: dict[str, str] | None = None
) -> tuple[Connection, dict[str, str]]:
    try:
        conn = CONNECTIONS[harness]
    except KeyError as exc:
        raise KeyError(
            f"unknown harness connection {harness!r}; known: {sorted(CONNECTIONS)}"
        ) from exc
    return conn, conn.resolve(env)
