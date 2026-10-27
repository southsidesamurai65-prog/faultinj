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
    answer: str | None = None  # extracted model answer, if any


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
    # chat-style tasks (math / knowledge / long-context) carry their gold answer
    # and arbitrary metadata; container tasks may leave these empty.
    answer: str | None = None
    metadata: dict = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)


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
    run_dir: str | None = None
    final_answer: str | None = None
    model: str | None = None
