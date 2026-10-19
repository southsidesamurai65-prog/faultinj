"""Harbor custom agent: Harbor's official Codex agent + fault injection.

Codex has no opencode-style tool hook, so injection is done at the *shell*
level: read-like commands (`cat`, `ls`, `grep`, ...) are shadowed by wrappers
under ``/tmp/fault-bin`` that pipe their stdout through ``fault_shim.py``.
Codex's shell picks the wrappers up because ``shell_environment_policy``
prepends that directory to ``PATH``.

Only observation (context) injection is supported; parameter-level injection
would require wrapping the command string and is left as a TODO.

Imported by Harbor only (imports ``harbor``); never by ``import pipe``.
"""

from __future__ import annotations

from typing import Annotated, override

from pydantic import Field

from harbor.agents.installed.codex import Codex, CodexOptions
from harbor.agents.options import Cli
from harbor.environments.base import BaseEnvironment

from .._shim import install_shim_command, shell_policy
from ..models import resolve_connection


class FaultCodexOptions(CodexOptions):
    fault_enabled: bool = Field(default=False)
    fault_site: str = Field(default="context", description="only 'context' is supported")
    fault_mode: str = Field(default="single", description="'single' | 'burst'")
    fault_width: int = Field(default=8)
    fault_prob: float = Field(default=0.0)
    fault_seed: int = Field(default=1)
    fault_prebuilt: bool = Field(default=False, description="skip codex install")
    fault_wire_api: str | None = Field(
        default=None, description="optional provider wire_api override (e.g. 'responses')"
    )
    # Harbor forces --enable unified_exec; counter it (placed after) so codex uses
    # one-shot exec instead of a persistent session that needs a TTY.
    disable_unified_exec: Annotated[
        bool, Cli("--disable", format="--disable unified_exec")
    ] = Field(default=True)


class FaultCodex(Codex):
    options_model = FaultCodexOptions
    options: FaultCodexOptions

    @override
    def _load_base_config(self) -> dict:
        config = super()._load_base_config()
        config.setdefault("shell_environment_policy", {}).update(shell_policy())

        # Optional: route through a custom provider (only when wire_api is set).
        # By default Harbor's built-in "openai" provider is used with
        # OPENAI_BASE_URL (set by the backend), i.e. the Responses API.
        if self.options.fault_wire_api:
            _, conn = resolve_connection("codex")
            provider: dict = {
                "name": "pipe",
                "wire_api": self.options.fault_wire_api,
                "env_key": "OPENAI_API_KEY",
            }
            if conn.get("base_url"):
                provider["base_url"] = conn["base_url"]
            config.setdefault("model_providers", {})["pipe"] = provider
            config["model_provider"] = "pipe"
        return config

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        if not self.options.fault_prebuilt:
            await super().install(environment)

        fault_cfg = {
            "enabled": self.options.fault_enabled,
            "site": self.options.fault_site,
            "mode": self.options.fault_mode,
            "width": self.options.fault_width,
            "prob": self.options.fault_prob,
            "seed": self.options.fault_seed,
        }
        result = await self.exec_as_agent(
            environment, command=install_shim_command(fault_cfg)
        )
        if getattr(result, "stdout", None):
            self.logger.info(f"[fault-codex] installed: {result.stdout.strip()}")

    @staticmethod
    @override
    def name() -> str:
        return "fault-codex"
