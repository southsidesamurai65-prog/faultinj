"""Generic Docker backend: run a task inside its container image.

Unlike the Harbor backend (which needs Harbor-format tasks), this runs a plain
``docker run`` for any task that carries an ``image`` (e.g. NL2Repo). Fault
injection is installed the same way as in the Harbor agents:

  * opencode -> project/global plugin + fault.json + provider overlay
  * codex    -> PATH shim under /tmp/fault-bin (see ``_shim``)

The verifier runs the task's ``verify_commands`` inside the container; the
reward is parsed from the pytest summary (continuous) or the exit code (binary).
"""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ._shim import DEFAULT_PATH, FAULT_BIN, install_shim_command
from .injector import InjectOptions
from .imagebuild import build_image, default_tag
from .models import MODELS, ModelSpec, resolve_connection
from .types import RunResult, Task

_PLUGIN = Path(__file__).resolve().parent / "assets" / "opencode-fault-inject.ts"
PROVIDER = "pipe"


@dataclass
class DockerConfig:
    harness: str = "opencode"  # opencode | codex
    home: str = "/root"
    name_prefix: str = "pipe"
    network: str | None = None
    extra_run_args: list[str] = field(default_factory=list)
    agent_setup: list[str] = field(default_factory=list)  # install agent in container
    proxy: str | None = None  # else PIPE_CONTAINER_PROXY (reachable from containers)
    build_image: bool = False  # prebuild task image with agent toolchain baked in
    force_build: bool = False


def _parse_reward(text: str, rc: int) -> tuple[float, str | None]:
    passed = sum(int(x) for x in re.findall(r"(\d+) passed", text))
    failed = sum(int(x) for x in re.findall(r"(\d+) failed", text))
    total = passed + failed
    if total > 0:
        return passed / total, f"{passed}/{total}"
    return (1.0 if rc == 0 else 0.0), None


class DockerBackend:
    name = "docker"

    def __init__(self, config: DockerConfig | None = None) -> None:
        self.config = config or DockerConfig()

    # ---- pure builders (testable) --------------------------------------
    def container_name(self, task: Task) -> str:
        return f"{self.config.name_prefix}-{task.name}-{uuid.uuid4().hex[:8]}"

    def build_create(self, task: Task, name: str, image: str | None = None) -> list[str]:
        cmd = ["docker", "run", "-d", "--name", name, "-w", task.container_workdir]
        if self.config.network:
            cmd += ["--network", self.config.network]
        cmd += self.config.extra_run_args
        cmd += [image or str(task.image), "sleep", "infinity"]
        return cmd

    def proxy_url(self) -> str | None:
        return self.config.proxy or os.environ.get("PIPE_CONTAINER_PROXY") or None

    def resolved_image(self, task: Task, build: bool = True) -> str:
        base = str(task.image)
        if not self.config.build_image:
            return base
        tag = default_tag(base, self.config.harness)
        if not build:
            return tag
        return build_image(
            base, agent=self.config.harness, tag=tag,
            proxy=self.proxy_url(), force=self.config.force_build,
        )

    def build_exec(self, name: str, command: str, env: dict[str, str] | None = None) -> list[str]:
        cmd = ["docker", "exec"]
        for k, v in (env or {}).items():
            cmd += ["-e", f"{k}={v}"]
        cmd += [name, "bash", "-lc", command]
        return cmd

    def install_command(self, model: ModelSpec, inject: InjectOptions) -> str:
        if self.config.harness == "codex":
            return install_shim_command(inject.config_dict())
        home = self.config.home
        _, conn = resolve_connection("opencode")
        options: dict[str, str] = {}
        if conn.get("base_url"):
            options["baseURL"] = conn["base_url"]
        if conn.get("api_key"):
            options["apiKey"] = conn["api_key"]
        provider = {
            "provider": {
                PROVIDER: {"options": options, "models": {m.wire_model: {} for m in MODELS.values()}}
            }
        }
        b64_plugin = base64.b64encode(_PLUGIN.read_text().encode()).decode()
        b64_cfg = base64.b64encode(json.dumps(inject.config_dict()).encode()).decode()
        b64_prov = base64.b64encode(json.dumps(provider).encode()).decode()
        return (
            "set -e; "
            f"mkdir -p {home}/.config/opencode/plugins {home}/.pipe; "
            f"echo {shlex.quote(b64_plugin)} | base64 -d "
            f"> {home}/.config/opencode/plugins/fault-inject.ts; "
            f"echo {shlex.quote(b64_cfg)} | base64 -d > {home}/.config/opencode/fault.json; "
            f"echo {shlex.quote(b64_prov)} | base64 -d > {home}/.pipe/opencode.json; "
            "echo '[fault-docker] opencode artifacts installed'"
        )

    def agent_command(self, task: Task, model: ModelSpec) -> str:
        if self.config.harness == "codex":
            return (
                "codex exec --model "
                f"{shlex.quote(model.wire_model)} "
                "--dangerously-bypass-approvals-and-sandbox --skip-git-repo-check "
                f"-- {shlex.quote(task.instruction)}"
            )
        return (
            "opencode run --model "
            f"{shlex.quote(model.id_for(PROVIDER))} --format json "
            "--dangerously-skip-permissions -- "
            f"{shlex.quote(task.instruction)}"
        )

    def agent_env(self, model: ModelSpec) -> dict[str, str]:
        if self.config.harness == "codex":
            _, conn = resolve_connection("codex")
            env = {"PATH": f"{FAULT_BIN}:{DEFAULT_PATH}", "FAULT_CONFIG": f"{FAULT_BIN}/fault.json"}
            if conn.get("api_key"):
                env["OPENAI_API_KEY"] = conn["api_key"]
            if conn.get("base_url"):
                env["OPENAI_BASE_URL"] = conn["base_url"]
            return env
        return {
            "OPENCODE_CONFIG": f"{self.config.home}/.pipe/opencode.json",
            "FAULT_CONFIG": f"{self.config.home}/.config/opencode/fault.json",
        }

    def proxy_env(self) -> dict[str, str]:
        p = self.proxy_url()
        if not p:
            return {}
        return {
            "http_proxy": p, "https_proxy": p, "HTTP_PROXY": p, "HTTPS_PROXY": p,
            "no_proxy": "localhost,127.0.0.1", "NO_PROXY": "localhost,127.0.0.1",
        }

    # ---- orchestration --------------------------------------------------
    def run(
        self, task: Task, model: ModelSpec, inject: InjectOptions, dry_run: bool = False
    ) -> RunResult:
        if not task.image:
            raise ValueError(f"task {task.name!r} has no container image")
        name = self.container_name(task)
        image = self.resolved_image(task, build=not dry_run)
        create = self.build_create(task, name, image)
        agent = self.build_exec(name, self.agent_command(task, model), self.agent_env(model))
        verify = self.build_exec(
            name, " && ".join(task.verify_commands) or "true", self.proxy_env()
        )
        if dry_run:
            return RunResult(
                task=task.name, reward=0.0, partial=None, detail="dry-run",
                steps=0, injected_calls=0, total_flips=0,
                error=f"create={' '.join(create)} agent={' '.join(agent)} verify={' '.join(verify)}",
            )

        env = self.proxy_env()
        subprocess.run(create, capture_output=True, text=True)
        try:
            if self.config.agent_setup:
                setup_cmd = " && ".join(self.config.agent_setup)
                subprocess.run(
                    self.build_exec(name, setup_cmd, env), capture_output=True, text=True
                )
            subprocess.run(
                self.build_exec(name, self.install_command(model, inject), env),
                capture_output=True, text=True,
            )
            for setup in task.setup:
                subprocess.run(self.build_exec(name, setup, env), capture_output=True, text=True)

            subprocess.run(agent, capture_output=True, text=True)

            # verifier runs with a clean env (no fault PATH / fault config)
            proc = subprocess.run(verify, capture_output=True, text=True)
            reward, partial = _parse_reward(proc.stdout + proc.stderr, proc.returncode)
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)

        return RunResult(
            task=task.name, reward=reward, partial=partial, detail="docker",
            steps=0, injected_calls=0, total_flips=0, error=None,
        )
