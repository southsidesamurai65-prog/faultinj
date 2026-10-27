"""Generic Docker backend: run a task inside its container image.

Unlike the Harbor backend (which needs Harbor-format tasks), this runs a plain
``docker run`` for any task that carries an ``image`` (e.g. NL2Repo). Fault
injection is installed the same way as in the Harbor agent: a project/global
plugin + fault.json + provider overlay.  No shell shim or HTTP proxy is used.

The verifier runs the task's ``verify_commands`` inside the container; the
reward is parsed from the pytest summary (continuous) or the exit code (binary).
"""

from __future__ import annotations

import base64
import json
import re
import shlex
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .injector import InjectOptions
from .runio import parse_agent_output, read_injection_stats, run_dir_name, scheme_sites
from .imagebuild import build_image, default_tag
from .models import MODELS, ModelSpec, resolve_connection
from .types import RunResult, Task

_PLUGIN = Path(__file__).resolve().parent / "assets" / "opencode-fault-inject.ts"
PROVIDER = "pipe"


@dataclass
class DockerConfig:
    harness: str = "opencode"  # only opencode is supported
    home: str = "/root"
    name_prefix: str = "pipe"
    network: str | None = None
    extra_run_args: list[str] = field(default_factory=list)
    agent_setup: list[str] = field(default_factory=list)  # install agent in container
    build_image: bool = False  # prebuild task image with agent toolchain baked in
    force_build: bool = False
    # host-side opencode assets; only needed for provider=opencode-go
    opencode_bin: str | None = None
    auth_file: str | None = None
    runs_dir: str = "runs/docker"
    inject_user: bool = True
    inject_tool: bool = False
    inject_assistant: bool = False
    protect_tests: bool = True
    agent_timeout: int = 1800
    verify_timeout: int = 1800
    keep_container: bool = False


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
        if self.config.harness != "opencode":
            raise ValueError("DockerBackend only supports harness='opencode'")

    # ---- pure builders (testable) --------------------------------------
    def container_name(self, task: Task) -> str:
        return f"{self.config.name_prefix}-{task.name}-{uuid.uuid4().hex[:8]}"

    def _opencode_assets(self, model: ModelSpec) -> tuple[str, str] | None:
        if self.config.harness != "opencode" or model.provider != "opencode-go":
            return None
        binary = self.config.opencode_bin or shutil.which("opencode")
        auth = self.config.auth_file or str(
            Path.home() / ".local" / "share" / "opencode" / "auth.json"
        )
        if not binary or not Path(binary).is_file():
            raise RuntimeError("opencode binary not found for container go provider")
        auth_path = Path(auth).expanduser()
        if not auth_path.is_file():
            raise RuntimeError(f"opencode auth file not found: {auth_path}")
        return str(Path(binary).resolve()), str(auth_path.resolve())

    def build_create(self, task: Task, name: str, image: str | None = None) -> list[str]:
        cmd = ["docker", "run", "-d", "--name", name, "-w", task.container_workdir]
        assets = getattr(self, "_assets", None)
        if assets:
            binary, auth = assets
            cmd += [
                "-v", f"{binary}:/usr/local/bin/opencode:ro",
                "-v", f"{auth}:/root/.local/share/opencode/auth.json:ro",
            ]
        if self.config.network:
            cmd += ["--network", self.config.network]
        cmd += self.config.extra_run_args
        cmd += [image or str(task.image), "sleep", "infinity"]
        return cmd

    def resolved_image(self, task: Task, build: bool = True) -> str:
        base = str(task.image)
        if not self.config.build_image:
            return base
        tag = default_tag(base, self.config.harness)
        if not build:
            return tag
        return build_image(
            base, agent=self.config.harness, tag=tag, force=self.config.force_build,
        )

    def build_exec(self, name: str, command: str, env: dict[str, str] | None = None) -> list[str]:
        cmd = ["docker", "exec"]
        for k, v in (env or {}).items():
            cmd += ["-e", f"{k}={v}"]
        cmd += [name, "bash", "-lc", command]
        return cmd

    def install_command(
        self, model: ModelSpec, inject: InjectOptions, *, run_id: str = "", task_id: str = ""
    ) -> str:
        home = self.config.home
        plugin_path = f"{home}/.config/opencode/plugins/fault-inject.ts"
        cfg = inject.config_dict()
        cfg["injectUser"] = self.config.inject_user
        cfg["injectTool"] = self.config.inject_tool
        cfg["injectAssistant"] = self.config.inject_assistant
        cfg["logPath"] = "/tmp/fault-inject.jsonl"
        cfg["runID"] = run_id
        cfg["taskID"] = task_id

        b64_plugin = base64.b64encode(_PLUGIN.read_text().encode()).decode()
        b64_cfg = base64.b64encode(json.dumps(cfg).encode()).decode()

        if model.provider == "opencode-go":
            model_id = model.id_for("opencode-go")
            opencode_cfg = {
                "$schema": "https://opencode.ai/config.json",
                "plugin": [plugin_path],
                "agent": {
                    "chat": {
                        "description": "Benchmark coding agent.",
                        "mode": "primary",
                        "model": model_id,
                        "temperature": 0.0,
                        "tools": {},
                    }
                },
                "model": model_id,
            }
            b64_config = base64.b64encode(json.dumps(opencode_cfg).encode()).decode()
        else:
            _, conn = resolve_connection("opencode")
            options: dict[str, str] = {}
            if conn.get("base_url"):
                options["baseURL"] = conn["base_url"]
            if conn.get("api_key"):
                options["apiKey"] = conn["api_key"]
            provider = {
                "provider": {
                    PROVIDER: {
                        "options": options,
                        "models": {m.wire_model: {} for m in MODELS.values()},
                    }
                }
            }
            b64_config = base64.b64encode(json.dumps(provider).encode()).decode()

        return (
            "set -e; "
            f"mkdir -p {home}/.config/opencode/plugins {home}/.pipe; "
            f"echo {shlex.quote(b64_plugin)} | base64 -d > {plugin_path}; "
            f"echo {shlex.quote(b64_cfg)} | base64 -d > {home}/.config/opencode/fault.json; "
            f"echo {shlex.quote(b64_config)} | base64 -d > {home}/.pipe/opencode.json; "
            "echo '[fault-docker] opencode artifacts installed'"
        )

    def agent_command(self, task: Task, model: ModelSpec) -> str:
        provider = "opencode-go" if model.provider == "opencode-go" else PROVIDER
        return (
            "opencode run --model "
            f"{shlex.quote(model.id_for(provider))} --agent chat --format json --auto "
            f"--dir {shlex.quote(task.container_workdir)} -- "
            f"{shlex.quote(task.instruction)}"
        )

    def agent_env(self, model: ModelSpec) -> dict[str, str]:
        return {
            "OPENCODE_CONFIG": f"{self.config.home}/.pipe/opencode.json",
            "FAULT_CONFIG": f"{self.config.home}/.config/opencode/fault.json",
        }

    # ---- orchestration --------------------------------------------------
    def run(
        self, task: Task, model: ModelSpec, inject: InjectOptions, dry_run: bool = False
    ) -> RunResult:
        if not task.image:
            raise ValueError(f"task {task.name!r} has no container image")
        assets = self._opencode_assets(model)
        self._assets = assets
        name = self.container_name(task)
        image = self.resolved_image(task, build=not dry_run)
        create = self.build_create(task, name, image)
        scheme, sites = scheme_sites(
            inject.site.value,
            self.config.inject_user,
            self.config.inject_tool,
            self.config.inject_assistant,
        )
        run_id = run_dir_name(task, model, inject, scheme=scheme, sites=sites)
        root = (Path(self.config.runs_dir) / run_id).resolve()
        agent = self.build_exec(
            name,
            self.agent_command(task, model),
            self.agent_env(model),
        )
        verify_cmd = " && ".join(task.verify_commands) or "true"
        workdir = shlex.quote(task.container_workdir)
        restore_tests = (
            f"if [ -d /tmp/pipe-pristine-tests ]; then "
            f"rm -rf {workdir}/tests && cp -a /tmp/pipe-pristine-tests {workdir}/tests; fi"
            if self.config.protect_tests
            else "true"
        )
        verify = self.build_exec(
            name, f"cd {workdir} && {restore_tests} && {verify_cmd}"
        )
        install = self.build_exec(
            name,
            self.install_command(model, inject, run_id=run_id, task_id=task.name),
        )

        if dry_run:
            return RunResult(
                task=task.name, reward=0.0, partial=None, detail="dry-run",
                steps=0, injected_calls=0, total_flips=0,
                error=f"create={' '.join(create)} agent={' '.join(agent)} verify={' '.join(verify)}",
            )

        root.mkdir(parents=True, exist_ok=True)
        (root / "task.json").write_text(json.dumps({
            "name": task.name,
            "answer": task.answer,
            "metadata": task.metadata,
            "instruction": task.instruction,
            "model": model.name,
            "fault": {
                "mode": inject.fault.mode,
                "prob": inject.fault.prob,
                "width": inject.fault.width,
                "seed": inject.fault.seed,
            },
            "site": inject.site.value,
            "container": {"image": image, "workdir": task.container_workdir},
        }, indent=2))
        (root / "command.txt").write_text(
            "\n\n".join(" ".join(c) for c in (create, install, agent, verify)) + "\n"
        )

        started = time.time()
        agent_stdout = agent_stderr = ""
        verify_text = ""
        reward, partial = 0.0, None
        create_rc = 0
        agent_rc = 0
        verify_rc = 0
        try:
            proc = subprocess.run(create, capture_output=True, text=True, timeout=600)
            create_rc = proc.returncode
            if create_rc != 0:
                agent_stderr = (proc.stdout or "") + (proc.stderr or "")
                raise RuntimeError("docker run failed")
            if self.config.agent_setup:
                setup_cmd = " && ".join(self.config.agent_setup)
                subprocess.run(self.build_exec(name, setup_cmd), capture_output=True, text=True, timeout=600)
            subprocess.run(install, capture_output=True, text=True, timeout=120)
            if self.config.protect_tests:
                subprocess.run(
                    self.build_exec(
                        name,
                        f"if [ -d {workdir}/tests ]; then rm -rf /tmp/pipe-pristine-tests && "
                        f"cp -a {workdir}/tests /tmp/pipe-pristine-tests; fi",
                    ),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            for setup in task.setup:
                subprocess.run(self.build_exec(name, setup), capture_output=True, text=True, timeout=600)

            try:
                proc = subprocess.run(
                    agent, capture_output=True, text=True, timeout=self.config.agent_timeout
                )
                agent_stdout, agent_stderr, agent_rc = proc.stdout, proc.stderr, proc.returncode
            except subprocess.TimeoutExpired as exc:
                agent_stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
                agent_stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
                agent_rc = -9

            # pull the fault log out before removing the container
            subprocess.run(
                ["docker", "cp", f"{name}:/tmp/fault-inject.jsonl", str(root / "injections.jsonl")],
                capture_output=True, text=True,
            )

            try:
                proc = subprocess.run(
                    verify, capture_output=True, text=True, timeout=self.config.verify_timeout
                )
                verify_text = (proc.stdout or "") + (proc.stderr or "")
                verify_rc = proc.returncode
                reward, partial = _parse_reward(verify_text, verify_rc)
            except subprocess.TimeoutExpired as exc:
                verify_text = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
                verify_text += (exc.stderr or "") if isinstance(exc.stderr, str) else ""
                verify_rc = -9
        except Exception as exc:  # keep diagnostics in the run dir
            agent_stderr += f"\n[docker-backend] {exc}"
        finally:
            if not self.config.keep_container:
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)

        elapsed = time.time() - started
        (root / "agent.jsonl").write_text(agent_stdout)
        (root / "agent.err").write_text(agent_stderr)
        (root / "verify.log").write_text(verify_text)
        answer, tokens = parse_agent_output(agent_stdout)
        injections, flips = read_injection_stats(root / "injections.jsonl")
        result = {
            "task": task.name,
            "model": model.name,
            "reward": reward,
            "partial": partial,
            "detail": f"exit={verify_rc}",
            "final_answer": answer,
            "gold": task.answer,
            "tokens": tokens,
            "returncode": agent_rc,
            "create_rc": create_rc,
            "verify_rc": verify_rc,
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
        try:
            from .analysis import analyze_run
            from .report import generate_report

            analyze_run(root)
            generate_report(root)
        except Exception as exc:
            (root / "analysis.err").write_text(repr(exc))

        return RunResult(
            task=task.name,
            reward=reward,
            partial=partial,
            detail=f"exit={verify_rc}",
            steps=0,
            injected_calls=injections,
            total_flips=flips,
            error=None if agent_rc == 0 else f"exit={agent_rc}",
            run_dir=str(root),
            final_answer=answer,
            model=model.name,
        )
