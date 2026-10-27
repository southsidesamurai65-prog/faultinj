"""Host-side chat backend (opencode + fault-injection plugin).

This is deliberately *not* an HTTP proxy: opencode's free tier rejects
proxied traffic, so the first pilot injects through opencode's plugin hooks
instead.  Math / knowledge / long-context tasks are single-shot ``opencode
run`` calls; the plugin corrupts the user prompt through ``chat.message``.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .injector import InjectOptions
from .models import ModelSpec
from .runio import parse_agent_output, read_injection_stats, run_dir_name, scheme_sites, session_id
from .types import RunResult, Task

_PLUGIN = Path(__file__).resolve().parent / "assets" / "opencode-fault-inject.ts"

# An explicitly empty tool map keeps opencode's default tools but avoids the
# "all tools disabled" path, which the free tier rejects.
_DEFAULT_TOOLS: dict[str, bool] = {}


@dataclass
class ChatConfig:
    opencode_bin: str = "opencode"
    runs_dir: str = "runs"
    timeout: int = 900
    agent: str = "chat"
    inject_user: bool = True
    inject_tool: bool = True
    inject_assistant: bool = False
    clear_proxy_env: bool = True  # strip inherited proxy vars; no proxy path exists
    auto_approve: bool = True
    max_retries: int = 3
    retry_sleep: float = 5.0
    extra_args: list[str] = field(default_factory=list)


class ChatBackend:
    name = "chat"

    def __init__(self, config: ChatConfig | None = None) -> None:
        self.config = config or ChatConfig()

    # ---- pure builders (testable) --------------------------------------
    def fault_config(
        self, inject: InjectOptions, *, run_id: str, task_id: str, log_path: Path
    ) -> dict[str, Any]:
        return {
            "enabled": inject.fault.prob > 0,
            "site": inject.site.value,
            "tools": inject.tools,
            "mode": inject.fault.mode,
            "width": inject.fault.width,
            "prob": inject.fault.prob,
            "seed": inject.fault.seed,
            "logPath": str(log_path),
            "injectUser": self.config.inject_user and inject.site.value == "context",
            "injectTool": self.config.inject_tool,
            "injectAssistant": self.config.inject_assistant,
            "runID": run_id,
            "taskID": task_id,
        }

    def opencode_config(self, task: Task, model: ModelSpec) -> dict[str, Any]:
        model_id = model.id_for(model.provider)
        return {
            "$schema": "https://opencode.ai/config.json",
            "plugin": [str(_PLUGIN)],
            "agent": {
                self.config.agent: {
                    "description": "Benchmark answer agent.",
                    "mode": "primary",
                    "model": model_id,
                    "temperature": 0.0,
                    "tools": dict(_DEFAULT_TOOLS),
                }
            },
            "model": model_id,
        }

    def command(
        self,
        task: Task,
        model: ModelSpec,
        workdir: Path,
        *,
        prompt: str | None = None,
        session: str | None = None,
    ) -> list[str]:
        cmd = [
            self.config.opencode_bin,
            "run",
            "--model",
            model.id_for(model.provider),
            "--agent",
            self.config.agent,
            "--format",
            "json",
            "--dir",
            str(workdir),
            *(["--auto"] if self.config.auto_approve else []),
            *self.config.extra_args,
        ]
        if session:
            cmd += ["--session", session]
        cmd += ["--", prompt if prompt is not None else task.instruction]
        return cmd

    def _env(self, config_path: Path, fault_path: Path, run_id: str, task_id: str) -> dict[str, str]:
        env = dict(os.environ)
        if self.config.clear_proxy_env:
            for key in (
                "http_proxy",
                "https_proxy",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "all_proxy",
            ):
                env.pop(key, None)
            env["NO_PROXY"] = "*"
            env["no_proxy"] = "*"
        env["OPENCODE_CONFIG"] = str(config_path.resolve())
        env["FAULT_CONFIG"] = str(fault_path.resolve())
        env["FAULT_RUN_ID"] = run_id
        env["FAULT_TASK_ID"] = task_id
        return env

    # ---- orchestration --------------------------------------------------
    def run(
        self, task: Task, model: ModelSpec, inject: InjectOptions, dry_run: bool = False
    ) -> RunResult:
        scheme, sites = scheme_sites(
            inject.site.value,
            self.config.inject_user,
            self.config.inject_tool,
            self.config.inject_assistant,
        )
        run_id = run_dir_name(task, model, inject, scheme=scheme, sites=sites)
        root = (Path(self.config.runs_dir) / run_id).resolve()
        work = root / "work"
        config_path = root / "opencode.json"
        fault_path = root / "fault.json"
        log_path = root / "injections.jsonl"
        command = self.command(task, model, work)
        followup = task.metadata.get("followup")

        if dry_run:
            return RunResult(
                task=task.name,
                reward=0.0,
                partial=None,
                detail="dry-run",
                steps=0,
                injected_calls=0,
                total_flips=0,
                error=f"cmd={' '.join(command)}",
                run_dir=str(root),
            )

        root.mkdir(parents=True, exist_ok=True)
        work.mkdir(parents=True, exist_ok=True)
        for rel, content in task.files.items():
            fixture = work / rel
            fixture.parent.mkdir(parents=True, exist_ok=True)
            fixture.write_text(content)
        config_path.write_text(json.dumps(self.opencode_config(task, model), indent=2))
        fault_path.write_text(
            json.dumps(self.fault_config(inject, run_id=run_id, task_id=task.name, log_path=log_path))
        )
        (root / "task.json").write_text(
            json.dumps(
                {
                    "name": task.name,
                    "answer": task.answer,
                    "metadata": task.metadata,
                    "instruction": task.instruction,
                    "followup": followup,
                    "model": model.name,
                    "fault": {
                        "mode": inject.fault.mode,
                        "prob": inject.fault.prob,
                        "width": inject.fault.width,
                        "seed": inject.fault.seed,
                    },
                    "site": inject.site.value,
                },
                indent=2,
            )
        )

        env = self._env(config_path, fault_path, run_id, task.name)
        started = time.time()
        attempt = 0
        while True:
            # A retry must not append a second copy of the same injection log.
            if attempt > 0:
                for name in ("injections.jsonl", "agent.jsonl", "agent.err"):
                    path = root / name
                    if path.is_file():
                        path.unlink()
                time.sleep(self.config.retry_sleep * attempt)
            try:
                proc = subprocess.run(
                    command,
                    cwd=work,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self.config.timeout,
                )
                stdout, stderr, rc = proc.stdout, proc.stderr, proc.returncode
            except subprocess.TimeoutExpired as exc:
                stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
                stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
                rc = -9
            attempt += 1
            if not _retryable(stdout, stderr, rc) or attempt >= self.config.max_retries:
                break
        commands = [command]
        combined_stdout = stdout
        combined_stderr = stderr
        final_stdout = stdout
        if followup and rc == 0 and not dry_run:
            session = session_id(stdout)
            cmd2 = self.command(
                task, model, work, prompt=str(followup), session=session
            )
            commands.append(cmd2)
            try:
                proc = subprocess.run(
                    cmd2,
                    cwd=work,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self.config.timeout,
                )
                stdout2, stderr2, rc = proc.stdout, proc.stderr, proc.returncode
            except subprocess.TimeoutExpired as exc:
                stdout2 = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
                stderr2 = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
                rc = -9
            combined_stdout += "\n" + stdout2
            combined_stderr += "\n" + stderr2
            final_stdout = stdout2
            attempt += 1
        elapsed = time.time() - started

        (root / "agent.jsonl").write_text(combined_stdout)
        (root / "agent.err").write_text(combined_stderr)
        answer, tokens = parse_agent_output(final_stdout)
        (work / "answer.txt").write_text(answer)
        ev = task.evaluator(work)
        injections, flips = read_injection_stats(log_path)

        result = {
            "task": task.name,
            "model": model.name,
            "reward": ev.reward,
            "partial": ev.partial,
            "detail": ev.detail,
            "final_answer": answer,
            "gold": task.answer,
            "tokens": tokens,
            "returncode": rc,
            "attempts": attempt,
            "elapsed_sec": round(elapsed, 3),
            "injections": injections,
            "flips": flips,
            "metadata": task.metadata,
            "fault": {
                "mode": inject.fault.mode,
                "prob": inject.fault.prob,
                "width": inject.fault.width,
                "seed": inject.fault.seed,
            },
            "site": inject.site.value,
            "inject_user": self.config.inject_user,
            "inject_tool": self.config.inject_tool,
            "inject_assistant": self.config.inject_assistant,
            "run_id": run_id,
        }
        (root / "result.json").write_text(json.dumps(result, indent=2))
        (root / "command.txt").write_text(
            "\n".join(" ".join(c) for c in commands) + "\n"
        )

        try:
            from .analysis import analyze_run

            analyze_run(root)
        except Exception as exc:  # analysis must not kill the experiment
            (root / "analysis.err").write_text(repr(exc))

        return RunResult(
            task=task.name,
            reward=ev.reward,
            partial=ev.partial,
            detail=ev.detail,
            steps=0,
            injected_calls=injections,
            total_flips=flips,
            error=None if rc == 0 else f"exit={rc}",
            run_dir=str(root),
            final_answer=answer,
            model=model.name,
        )


def _retryable(stdout: str, stderr: str, rc: int) -> bool:
    text = (stdout + "\n" + stderr).lower()
    if "endpoint is unavailable" in text or "rate limit" in text or "overloaded" in text:
        return True
    if "\"statuscode\":503" in text or "server_error" in text and "unavailable" in text:
        return True
    if rc == -9:  # timeout
        return True
    return False
