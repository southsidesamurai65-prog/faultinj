"""Harbor custom agent: Harbor's official OpenCode agent + fault injection.

Runs *inside Harbor's environment* (it imports ``harbor``), so this module is
imported only by Harbor, never by ``import pipe``. Harbor is pointed at it via
``--agent pipe.harbor_agent.fault_opencode:FaultOpenCode``.

It delegates install/run/scoring to Harbor's ``OpenCode`` agent and only adds:
  * copying the fault plugin (``pipe/assets/opencode-fault-inject.ts``)
  * writing ``fault.json`` from the ``fault_*`` kwargs
  * optionally skipping the toolchain install when ``fault_prebuilt`` is true
"""

from __future__ import annotations

import base64
import json
import shlex
from pathlib import Path
from typing import override

from pydantic import Field

from harbor.agents.installed.opencode import OpenCode, OpenCodeOptions
from harbor.environments.base import BaseEnvironment

_PLUGIN_PATH = Path(__file__).resolve().parent.parent / "assets" / "opencode-fault-inject.ts"


class FaultOpenCodeOptions(OpenCodeOptions):
    """Extra kwargs accepted via ``--ak key=value``."""

    fault_enabled: bool = Field(default=False, description="Enable bit-flip injection.")
    fault_site: str = Field(
        default="context", description="Injection site: 'context' | 'tool'."
    )
    fault_tools: str = Field(
        default="", description="Comma-separated tool allowlist (empty = all)."
    )
    fault_mode: str = Field(
        default="single", description="Fault model: 'single' (per-bit) or 'burst'."
    )
    fault_width: int = Field(default=8, description="Burst length in bytes.")
    fault_prob: float = Field(
        default=0.0, description="Trigger probability (per bit / per byte)."
    )
    fault_seed: int = Field(default=1, description="PRNG seed.")
    fault_prebuilt: bool = Field(
        default=False, description="Task image already ships node/npm/opencode; skip install."
    )
    inject_user: bool = Field(default=False, description="Inject the user prompt (context site).")
    inject_tool: bool = Field(default=True, description="Inject tool results (context site).")
    inject_assistant: bool = Field(
        default=False, description="Inject prior assistant text/tool calls."
    )


class FaultOpenCode(OpenCode):
    options_model = FaultOpenCodeOptions
    options: FaultOpenCodeOptions

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        if not self.options.fault_prebuilt:
            await super().install(environment)

        if not _PLUGIN_PATH.is_file():
            raise FileNotFoundError(f"plugin not found: {_PLUGIN_PATH}")

        tools = [t.strip() for t in (self.options.fault_tools or "").split(",") if t.strip()]
        fault_cfg = {
            "enabled": self.options.fault_enabled,
            "site": self.options.fault_site,
            "tools": tools,
            "mode": self.options.fault_mode,
            "width": self.options.fault_width,
            "prob": self.options.fault_prob,
            "seed": self.options.fault_seed,
            "logPath": "/logs/agent/fault-inject.log.jsonl",
            "injectUser": self.options.inject_user,
            "injectTool": self.options.inject_tool,
            "injectAssistant": self.options.inject_assistant,
        }

        b64_plugin = base64.b64encode(_PLUGIN_PATH.read_text().encode()).decode()
        b64_cfg = base64.b64encode(json.dumps(fault_cfg).encode()).decode()

        command = (
            "set -e; "
            "mkdir -p ~/.config/opencode/plugins /logs/agent; "
            f"echo {shlex.quote(b64_plugin)} | base64 -d "
            "> ~/.config/opencode/plugins/fault-inject.ts; "
            f"echo {shlex.quote(b64_cfg)} | base64 -d "
            "> ~/.config/opencode/fault.json; "
            "echo '[fault-inject] config:'; cat ~/.config/opencode/fault.json"
        )
        result = await self.exec_as_agent(environment, command=command)
        if getattr(result, "stdout", None):
            self.logger.info(f"[fault-inject] installed: {result.stdout.strip()}")

    @staticmethod
    @override
    def name() -> str:
        return "fault-opencode"
