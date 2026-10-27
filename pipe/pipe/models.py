"""Model registry and OpenCode connection endpoint.

Keys/URLs come from environment variables only (or from the Go provider's
local auth for ``*-go`` models).

    CONNECTIONS["opencode"] -> custom OpenCode endpoint (one url + one key)
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
    provider: str = "opencode"

    def id_for(self, provider: str | None = None) -> str:
        """opencode-style provider-qualified id (e.g. ``pipe/deepseek-v4.1-flash``)."""
        return f"{provider or self.provider}/{self.wire_model}"


CONNECTIONS: dict[str, Connection] = {
    "opencode": Connection(
        name="opencode",
        base_url_env="PIPE_OPENCODE_BASE_URL",
        base_url_default="https://REPLACE_ME/opencode",
        api_key_env="PIPE_OPENCODE_API_KEY",
    ),
}

MODELS: dict[str, ModelSpec] = {
    "deepseek-v4.1-flash": ModelSpec("deepseek-v4.1-flash", "deepseek-v4.1-flash"),
    # OpenCode's built-in providers.
    "mimo-v2.6-flash-free": ModelSpec("mimo-v2.6-flash-free", "mimo-v2.6-flash-free"),
    # OpenCode Go (uses the authenticated opencode-go provider).
    "mimo-v2.6-flash": ModelSpec("mimo-v2.6-flash", "mimo-v2.6-flash", provider="opencode-go"),
    "deepseek-v4.1-flash-go": ModelSpec(
        "deepseek-v4.1-flash-go", "deepseek-v4.1-flash", provider="opencode-go"
    ),
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
