"""Harbor backend: run tasks in their benchmark containers via Harbor.

Harbor is the official Terminal-Bench runner (container isolation, image pull,
agent install, scoring). This backend builds the ``harbor run`` invocation and
points Harbor at our OpenCode fault-injecting agent
(``pipe.harbor_agent.fault_opencode:FaultOpenCode``).  It uses a provider
overlay (``opencode_config``, model id ``pipe/<wire>``).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .injector import InjectOptions
from .models import MODELS, ModelSpec, resolve_connection
from .types import RunResult, Task

AGENT_IMPORT = "pipe.harbor_agent.fault_opencode:FaultOpenCode"
PROVIDER = "pipe"
_PKG_ROOT = Path(__file__).resolve().parent.parent  # importable project root


@dataclass
class HarborConfig:
    environment: str = "docker"
    n_concurrent: int = 1
    attempts: int = 1
    prebuilt: bool = False  # --ak fault_prebuilt=true when image ships the agent
    inject_user: bool = False
    inject_tool: bool = True
    inject_assistant: bool = False
    results_dir: str = "harbor_results"
    memory_mb: int | None = None  # --override-memory-mb
    cpus: int | None = None  # --override-cpus
    extra_args: list[str] = field(default_factory=list)


class HarborBackend:
    name = "harbor"

    def __init__(self, config: HarborConfig | None = None) -> None:
        self.config = config or HarborConfig()

    def opencode_config(self, model: ModelSpec) -> dict:
        _, conn = resolve_connection("opencode")
        options: dict[str, str] = {}
        if conn.get("base_url"):
            options["baseURL"] = conn["base_url"]
        if conn.get("api_key"):
            options["apiKey"] = conn["api_key"]
        return {
            "provider": {
                PROVIDER: {
                    "options": options,
                    "models": {m.wire_model: {} for m in MODELS.values()},
                }
            }
        }

    def _model_id(self, model: ModelSpec) -> str:
        return model.id_for(PROVIDER)

    def build_command(
        self, task: Task, model: ModelSpec, inject: InjectOptions, *, task_arg: str | None = None
    ) -> list[str]:
        cmd = [
            "harbor", "run",
            "-a", AGENT_IMPORT,
            "-m", self._model_id(model),
            "-e", self.config.environment,
            "-k", str(self.config.attempts),
            "-n", str(self.config.n_concurrent),
            "-o", self.config.results_dir,
            "--ak", "fault_enabled=true",
            "--ak", f"fault_site={inject.site.value}",
            "--ak", f"fault_mode={inject.fault.mode}",
            "--ak", f"fault_width={inject.fault.width}",
            "--ak", f"fault_prob={inject.fault.prob}",
            "--ak", f"fault_seed={inject.fault.seed}",
            "--ak", f"fault_prebuilt={str(self.config.prebuilt).lower()}",
            "--ak", f"inject_user={str(self.config.inject_user).lower()}",
            "--ak", f"inject_tool={str(self.config.inject_tool).lower()}",
            "--ak", f"inject_assistant={str(self.config.inject_assistant).lower()}",
        ]
        if inject.tools:
            cmd += ["--ak", f"fault_tools={','.join(inject.tools)}"]
        cfg_json = json.dumps(self.opencode_config(model), separators=(",", ":"))
        cmd += ["--ak", f"opencode_config={cfg_json}"]
        cmd += ["-p", task_arg or str(task.workdir), *self.config.extra_args]
        if self.config.memory_mb:
            cmd += ["--override-memory-mb", str(self.config.memory_mb)]
        if self.config.cpus:
            cmd += ["--override-cpus", str(self.config.cpus)]
        cmd += ["--yes"]
        return cmd

    def build_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = f"{_PKG_ROOT}{os.pathsep}{env.get('PYTHONPATH', '')}"
        return env

    def latest_reward(self) -> float | None:
        results = Path(self.config.results_dir)
        files = sorted(results.glob("**/result.json"), key=lambda p: p.stat().st_mtime)
        for f in reversed(files):
            try:
                data = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            verifier = data.get("verifier_result") or {}
            reward = (verifier.get("rewards") or {}).get("reward")
            if reward is not None:
                return float(reward)
        return None

    def run(
        self, task: Task, model: ModelSpec, inject: InjectOptions, dry_run: bool = False
    ) -> RunResult:
        cmd = self.build_command(task, model, inject)
        if dry_run:
            return RunResult(
                task=task.name, reward=0.0, partial=None, detail="dry-run",
                steps=0, injected_calls=0, total_flips=0,
                error=f"cmd={' '.join(cmd)}",
            )
        proc = subprocess.run(cmd, env=self.build_env(), capture_output=True, text=True)
        reward = self.latest_reward()
        return RunResult(
            task=task.name,
            reward=reward if reward is not None else 0.0,
            partial=None,
            detail=f"results={self.config.results_dir}",
            steps=0,
            injected_calls=0,
            total_flips=0,
            error=None if proc.returncode == 0 else f"exit={proc.returncode}",
        )
