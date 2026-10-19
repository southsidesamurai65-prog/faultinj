"""Core value types shared by the pipeline (backend-agnostic)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class EvalResult:
    reward: float
    partial: str | None = None
    detail: str | None = None


@dataclass
class Task:
    name: str
    instruction: str
    workdir: Path
    evaluator: Callable[[Path], EvalResult]
    image: str | None = None
    container_workdir: str = "/app"
    setup: list[str] = field(default_factory=list)
    verify_commands: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    task: str
    reward: float
    partial: str | None
    detail: str | None
    steps: int
    injected_calls: int
    total_flips: int
    error: str | None = None
