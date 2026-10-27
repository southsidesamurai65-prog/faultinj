"""Benchmark loaders: turn a benchmark directory into pipeline ``Task``s.

Real verifiers run inside task containers; the default evaluators here run the
verifier command(s) locally (adapt as needed).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
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



# ---------------------------------------------------------------------------
# Chat-style benchmarks (math / knowledge / long-context)
# ---------------------------------------------------------------------------

_MATH_PROMPT = (
    "{question}\n\nSolve the problem above. Do not use any tools. Show your "
    "reasoning if useful, then give the final answer as a single number on the "
    "last line in the format:\nAnswer: <number>\n"
)

_KNOWLEDGE_PROMPT = (
    "{question}\n{choices}\n\nAnswer the multiple-choice question above. Do "
    "not use any tools. Reply with only the letter of the correct option "
    "(A, B, C, or D).\n"
)

_LONG_CONTEXT_PROMPT = (
    "Read the document and answer the multiple-choice question. Do not use any "
    "tools. Reply with only the letter of the correct option (A, B, C, or D).\n\n"
    "<document>\n{context}\n</document>\n\n"
    "Question: {question}\n"
    "A. {choice_A}\nB. {choice_B}\nC. {choice_C}\nD. {choice_D}\n"
)


def extract_final_answer(text: str) -> str:
    """Best-effort final-answer extraction for chat benchmarks."""
    if not text:
        return ""
    m = re.findall(r"\\boxed\{([^{}]*)\}", text)
    if m:
        return m[-1].strip()
    m = re.findall(
        r"(?:final\s+answer|answer)\s*[:=]\s*([^\n]+)", text, flags=re.I
    )
    if m:
        return m[-1].strip().strip("$` ")
    # Multiple-choice: prefer a standalone capital letter near the end.
    m = re.findall(r"\b([A-J])\b", text)
    if m:
        return m[-1]
    # Last numeric token (handles "$1,234", "3.14", "-5", "12%").
    m = re.findall(r"-?\d[\d,]*(?:\.\d+)?", text)
    if m:
        return m[-1].replace(",", "")
    return text.strip().splitlines()[-1].strip()


def _normalise_number(value: str) -> float | None:
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace("$", "").replace("%", "")
    s = s.rstrip(".")
    try:
        return float(s)
    except ValueError:
        return None


def math_correct(pred: str, gold: str) -> bool:
    pred_s = str(pred).strip()
    gold_s = str(gold).strip()
    if pred_s.lower() == gold_s.lower():
        return True
    p = _normalise_number(pred_s)
    g = _normalise_number(gold_s)
    if p is not None and g is not None:
        return abs(p - g) <= 1e-9 * max(1.0, abs(g))
    return False


def choice_correct(pred: str, gold: str) -> bool:
    p = str(pred).strip().upper()
    g = str(gold).strip().upper()
    if p == g:
        return True
    # "Answer: C", "(C)", "C." etc.
    m = re.search(r"\b([A-J])\b", p)
    return bool(m and m.group(1) == g)


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _chat_files(path: str | Path) -> list[Path]:
    p = Path(path)
    if p.is_file():
        return [p]
    return sorted(p.glob("*.jsonl"))


def _make_chat_evaluator(gold: str, kind: str) -> Callable[[Path], EvalResult]:
    def evaluate(workdir: Path) -> EvalResult:
        answer_file = workdir / "answer.txt"
        text = answer_file.read_text() if answer_file.is_file() else ""
        pred = extract_final_answer(text)
        ok = math_correct(pred, gold) if kind == "math" else choice_correct(pred, gold)
        return EvalResult(
            1.0 if ok else 0.0,
            partial=None,
            detail=f"pred={pred!r} gold={gold!r}",
            answer=pred,
        )

    return evaluate


_CODING_PROMPT = (
    "{prompt}\n\n"
    "Write the complete implementation to `solution.py` in the current "
    "directory. You may inspect files and run commands in this directory. "
    "Do not use the network. Hidden tests will import `{entry_point}` from "
    "`solution.py` and run them after you finish.\n"
)


def _make_code_evaluator(entry_point: str, test: str, timeout: int = 30) -> Callable[[Path], EvalResult]:
    def evaluate(workdir: Path) -> EvalResult:
        solution = workdir / "solution.py"
        if not solution.is_file():
            return EvalResult(0.0, detail="solution.py missing")
        test_path = workdir / "_hidden_test.py"
        test_path.write_text(
            test
            + "\n\nfrom solution import "
            + entry_point
            + " as candidate\ncheck(candidate)\n"
        )
        try:
            proc = subprocess.run(
                [sys.executable, test_path.name],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return EvalResult(0.0, detail=f"test error: {exc}")
        detail = (proc.stdout + proc.stderr).strip()[-800:]
        return EvalResult(1.0 if proc.returncode == 0 else 0.0, detail=detail or f"exit={proc.returncode}")

    return evaluate


class _ChatBenchmark:
    kind = "chat"

    def __init__(self, path: str | Path, limit: int | None = None) -> None:
        self.path = Path(path)
        self._limit = limit

    def _rows(self) -> list[dict]:
        rows: list[dict] = []
        for f in _chat_files(self.path):
            rows.extend(_read_jsonl(f))
        return rows

    def tasks(self, limit: int | None = None) -> list[Task]:
        n = self._limit if limit is None else limit
        out: list[Task] = []
        for i, row in enumerate(self._rows()):
            if n is not None and len(out) >= n:
                break
            out.append(self._task(i, row))
        return out

    def _task(self, i: int, row: dict) -> Task:  # pragma: no cover - overridden
        raise NotImplementedError


class MathQA(_ChatBenchmark):
    """JSONL rows: ``{id?, question, answer}``."""

    name = "math"

    def _task(self, i: int, row: dict) -> Task:
        gold = str(row.get("answer", ""))
        question = str(row.get("question") or row.get("problem") or "")
        name = str(row.get("id") or f"math-{i}")
        instruction = _MATH_PROMPT.format(question=question)
        return Task(
            name=name,
            instruction=instruction,
            workdir=self.path.parent,
            evaluator=_make_chat_evaluator(gold, "math"),
            answer=gold,
            metadata={"kind": "math", "question": question, "source": row.get("source")},
        )


_MATH_REVIEW_PROMPT = (
    "Review the answer and reasoning above. Check for arithmetic or consistency "
    "errors, and correct them if needed. Do not use any tools. End with the "
    "final answer on the last line in the format:\nAnswer: <number>\n"
)


class MathSelfCheckQA(MathQA):
    """Two-turn GSM8K: solve once, then review the previous answer."""

    name = "math-selfcheck"

    def _task(self, i: int, row: dict) -> Task:
        task = super()._task(i, row)
        task.metadata["followup"] = _MATH_REVIEW_PROMPT
        return task


class KnowledgeQA(_ChatBenchmark):
    """JSONL rows: ``{id?, question, choices, answer}`` (answer = A/B/C/D)."""

    name = "knowledge"

    def _task(self, i: int, row: dict) -> Task:
        gold = str(row.get("answer", "")).strip()
        question = str(row.get("question", ""))
        choices = list(row.get("choices") or [])
        letters = "ABCD"
        lines = "\n".join(
            f"{letters[j]}. {c}" for j, c in enumerate(choices[:4])
        )
        instruction = _KNOWLEDGE_PROMPT.format(question=question, choices=lines)
        name = str(row.get("id") or f"knowledge-{i}")
        return Task(
            name=name,
            instruction=instruction,
            workdir=self.path.parent,
            evaluator=_make_chat_evaluator(gold, "choice"),
            answer=gold,
            metadata={
                "kind": "knowledge",
                "question": question,
                "choices": choices,
                "source": row.get("source"),
            },
        )


class LongContextQA(_ChatBenchmark):
    """JSONL rows: LongBench-v2 style ``{_id, context, question, choice_A..D, answer}``."""

    name = "longcontext"

    def _task(self, i: int, row: dict) -> Task:
        gold = str(row.get("answer", "")).strip()
        context = str(row.get("context", ""))
        question = str(row.get("question", ""))
        choice_a = str(row.get("choice_A", ""))
        choice_b = str(row.get("choice_B", ""))
        choice_c = str(row.get("choice_C", ""))
        choice_d = str(row.get("choice_D", ""))
        instruction = _LONG_CONTEXT_PROMPT.format(
            context=context,
            question=question,
            choice_A=choice_a,
            choice_B=choice_b,
            choice_C=choice_c,
            choice_D=choice_d,
        )
        name = str(row.get("_id") or row.get("id") or f"longcontext-{i}")
        return Task(
            name=name,
            instruction=instruction,
            workdir=self.path.parent,
            evaluator=_make_chat_evaluator(gold, "choice"),
            answer=gold,
            metadata={
                "kind": "longcontext",
                "question": question,
                "choices": [choice_a, choice_b, choice_c, choice_d],
                "domain": row.get("domain"),
                "sub_domain": row.get("sub_domain"),
                "length": row.get("length"),
                "context_chars": len(context),
            },
        )


_LONG_CONTEXT_REVIEW_PROMPT = (
    "Review the answer and explanation above using the document and question. "
    "Check whether the selected option is correct, and correct it if needed. "
    "Do not use any tools. End with the final answer on the last line in the "
    "format:\nAnswer: <letter>\n"
)


class LongContextSelfCheckQA(LongContextQA):
    """Two-turn long-context task with a reasoning-first first answer.

    E injects prior assistant messages.  The plain long-context prompt returns
    only a single letter, which is too short for a meaningful p=.001 probe, so
    this variant asks for a one-sentence explanation before the answer.
    """

    name = "longcontext-selfcheck"

    def _task(self, i: int, row: dict) -> Task:
        task = super()._task(i, row)
        task.instruction = task.instruction.replace(
            "Reply with only the letter of the correct option (A, B, C, or D).\n",
            "First explain your choice in one sentence, then give the final "
            "answer on the last line in the format:\nAnswer: <letter>\n",
        )
        task.metadata["followup"] = _LONG_CONTEXT_REVIEW_PROMPT
        return task


class CodeQA(_ChatBenchmark):
    """JSONL rows: ``{id, prompt, test, entry_point}`` (HumanEval style)."""

    name = "code"

    def _task(self, i: int, row: dict) -> Task:
        prompt = str(row.get("prompt") or row.get("instruction") or "")
        entry_point = str(row.get("entry_point") or "")
        test = str(row.get("test") or "")
        name = str(row.get("id") or row.get("task_id") or f"code-{i}")
        instruction = _CODING_PROMPT.format(prompt=prompt, entry_point=entry_point)
        return Task(
            name=name,
            instruction=instruction,
            workdir=self.path.parent,
            evaluator=_make_code_evaluator(entry_point, test),
            answer=entry_point,
            metadata={
                "kind": "code",
                "entry_point": entry_point,
                "source": row.get("source"),
            },
        )


BENCHMARKS = {
    "terminal-bench": TerminalBench,
    "nl2repo": NL2Repo,
    "math": MathQA,
    "math-selfcheck": MathSelfCheckQA,
    "knowledge": KnowledgeQA,
    "longcontext": LongContextQA,
    "longcontext-selfcheck": LongContextSelfCheckQA,
    "code": CodeQA,
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
