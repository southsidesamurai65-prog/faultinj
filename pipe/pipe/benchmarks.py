"""Benchmark loaders: turn a benchmark directory into pipeline ``Task``s.

Real verifiers run inside task containers; the default evaluators here run the
verifier command(s) locally (adapt as needed).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Callable, Protocol

from .types import EvalResult, Task

_CONTAINER_HINT = "runs in the task container"


def run_verifier(
    workdir: Path,
    command: list[str],
    reward_file: str = "reward.txt",
    timeout: int = 900,
) -> EvalResult:
    """Run a single verifier command in ``workdir`` and read its reward file."""
    try:
        proc = subprocess.run(
            command, cwd=workdir, capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return EvalResult(0.0, detail=f"verifier error: {exc}")

    rf = workdir / reward_file
    if rf.is_file():
        try:
            return EvalResult(float(rf.read_text().strip()), detail=f"reward_file={rf}")
        except ValueError:
            pass
    return EvalResult(1.0 if proc.returncode == 0 else 0.0, detail=f"exit={proc.returncode}")


def run_commands(workdir: Path, commands: list[str], timeout: int = 1800) -> EvalResult:
    """Run a list of shell commands; reward = pytest pass rate (continuous)."""
    outputs: list[str] = []
    last_rc = 0
    for cmd in commands:
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=workdir, capture_output=True, text=True, timeout=timeout
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return EvalResult(0.0, detail=f"verifier error: {exc}")
        outputs.append(proc.stdout + proc.stderr)
        last_rc = proc.returncode

    text = "\n".join(outputs)
    passed = sum(int(x) for x in re.findall(r"(\d+) passed", text))
    failed = sum(int(x) for x in re.findall(r"(\d+) failed", text))
    total = passed + failed
    if total > 0:
        return EvalResult(passed / total, partial=f"{passed}/{total}", detail="pytest pass rate")
    return EvalResult(1.0 if last_rc == 0 else 0.0, detail=f"exit={last_rc}")


class Benchmark(Protocol):
    name: str

    def tasks(self, limit: int | None = None) -> list[Task]: ...


class TerminalBench:
    """Terminal-Bench 2.x: ``tasks/<name>/{task.toml,instruction.md,tests/}``."""

    name = "terminal-bench"

    def __init__(self, tasks_dir: str | Path, verifier: str = "tests/test.sh") -> None:
        self.tasks_dir = Path(tasks_dir)
        self.verifier = verifier

    def tasks(self, limit: int | None = None) -> list[Task]:
        if (self.tasks_dir / "task.toml").is_file():
            candidates = [self.tasks_dir]  # path itself is a single task
        else:
            candidates = sorted(p for p in self.tasks_dir.iterdir() if p.is_dir())
        out: list[Task] = []
        for d in candidates:
            if not (d / "task.toml").is_file():
                continue
            toml_text = (d / "task.toml").read_text()
            m = re.search(r'docker_image\s*=\s*"([^"]+)"', toml_text)
            out.append(
                Task(
                    name=d.name,
                    instruction=(d / "instruction.md").read_text(),
                    workdir=d,
                    evaluator=lambda wd: run_verifier(wd, ["bash", self.verifier], "reward.txt"),
                    image=m.group(1) if m else None,
                    verify_commands=[f"bash {self.verifier}"],
                )
            )
            if limit is not None and len(out) >= limit:
                break
        return out


class NL2Repo:
    """NL2Repo-Bench: ``test_files/<lib>/{start.md,test_commands.json,...}``.

    Agent starts from an empty workspace and must build the library; the verifier
    runs the task's commands (which need the task's original test files, normally
    provided by the per-task container image).
    """

    name = "nl2repo"

    def __init__(
        self,
        test_files_dir: str | Path,
        instruction_name: str = "start.md",
        commands_name: str = "test_commands.json",
        image_template: str | None = None,
        container_workdir: str = "/app",
    ) -> None:
        self.test_files_dir = Path(test_files_dir)
        self.instruction_name = instruction_name
        self.commands_name = commands_name
        self.image_template = image_template
        self.container_workdir = container_workdir

    def tasks(self, limit: int | None = None) -> list[Task]:
        out: list[Task] = []
        for d in sorted(p for p in self.test_files_dir.iterdir() if p.is_dir()):
            instruction = d / self.instruction_name
            commands_f = d / self.commands_name
            if not instruction.is_file() or not commands_f.is_file():
                continue
            commands = json.loads(commands_f.read_text())
            image = self.image_template.format(name=d.name) if self.image_template else None
            out.append(
                Task(
                    name=d.name,
                    instruction=instruction.read_text(),
                    workdir=d,
                    evaluator=_make_command_evaluator(commands),
                    image=image,
                    container_workdir=self.container_workdir,
                    verify_commands=commands,
                )
            )
            if limit is not None and len(out) >= limit:
                break
        return out


BENCHMARKS = {
    "terminal-bench": TerminalBench,
    "nl2repo": NL2Repo,
}


def _make_command_evaluator(commands: list[str]) -> Callable[[Path], EvalResult]:
    def evaluate(workdir: Path) -> EvalResult:
        return run_commands(workdir, commands)

    return evaluate


def make_benchmark(name: str, path: str | Path, **kwargs) -> Benchmark:
    try:
        cls = BENCHMARKS[name]
    except KeyError as exc:
        raise KeyError(f"unknown benchmark {name!r}; known: {sorted(BENCHMARKS)}") from exc
    return cls(path, **kwargs)
