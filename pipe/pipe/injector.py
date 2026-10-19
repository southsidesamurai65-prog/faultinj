"""Injection middleware.

A ``FaultInjector`` is a pair of hooks a harness calls around every tool
interaction. It decides *where* to inject via ``Site``:

    CONTEXT -> every tool result (the text the agent observes)
    TOOL    -> tool arguments (what the agent writes/executes)

The injector contains no harness- or model-specific logic; adding a new
harness only requires calling ``before_tool`` / ``after_tool`` at the right
points.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from .faults import FaultConfig, apply_fault, mulberry32


class Site(str, Enum):
    CONTEXT = "context"
    TOOL = "tool"


@dataclass
class InjectionRecord:
    tool: str
    site: str
    field: str | None
    n_flips: int
    n_bytes: int
    before: str
    after: str


class FaultInjector:
    """Applies a fault model to tool results and/or tool arguments."""

    def __init__(
        self,
        cfg: FaultConfig,
        site: Site,
        tools: list[str] | None = None,
        log_path: str | Path | None = None,
    ) -> None:
        self.cfg = cfg
        self.site = site
        self.tools = set(tools or [])
        self.rng: Callable[[], float] = mulberry32(cfg.seed)
        self.log_path = Path(log_path) if log_path else None
        self.records: list[InjectionRecord] = []

    def _enabled_for(self, tool: str) -> bool:
        return not self.tools or tool in self.tools

    def _inject(self, tool: str, text: str, field: str | None) -> str:
        if not self._enabled_for(tool) or self.cfg.prob <= 0:
            return text
        res = apply_fault(text, self.cfg, self.rng)
        if res.n_flips == 0:
            return text
        rec = InjectionRecord(
            tool=tool,
            site=self.site.value,
            field=field,
            n_flips=res.n_flips,
            n_bytes=len(text.encode("utf-8")),
            before=text,
            after=res.text,
        )
        self.records.append(rec)
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as fh:
                fh.write(json.dumps(asdict(rec)) + "\n")
        return res.text

    def after_tool(self, tool: str, output: str) -> str:
        """Hook called with the raw tool output; returns what the agent sees."""
        if self.site is not Site.CONTEXT:
            return output
        return self._inject(tool, output, field=None)

    def before_tool(self, tool: str, args: dict[str, Any], field: str) -> dict[str, Any]:
        """Hook called with tool arguments; returns (possibly faulted) args."""
        if self.site is not Site.TOOL:
            return args
        value = args.get(field)
        if not isinstance(value, str):
            return args
        args[field] = self._inject(tool, value, field=field)
        return args

    @property
    def injected_calls(self) -> int:
        return len(self.records)

    @property
    def total_flips(self) -> int:
        return sum(r.n_flips for r in self.records)


@dataclass
class InjectOptions:
    """Fault-injection configuration passed to a backend/agent."""

    fault: FaultConfig
    site: Site = Site.CONTEXT
    tools: list[str] = field(default_factory=list)
    log_path: str = "fault-inject.log.jsonl"

    def config_dict(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "site": self.site.value,
            "tools": self.tools,
            "mode": self.fault.mode,
            "width": self.fault.width,
            "prob": self.fault.prob,
            "seed": self.fault.seed,
            "logPath": self.log_path,
        }


def read_injection_stats(log_path: Path) -> tuple[int, int]:
    """Return (injected_calls, total_flips) from a JSONL injection log."""
    calls = flips = 0
    if log_path.is_file():
        for line in log_path.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            calls += 1
            flips += int(rec.get("n_flips", 0))
    return calls, flips
