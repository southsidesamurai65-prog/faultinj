"""Run configuration (YAML/JSON) and the top-level run pipeline.

``backend`` picks the runner:

    backend: harbor | docker | chat

All backends use the OpenCode plugin (hook-based injection).  No HTTP proxy
or shell shim is involved.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from .benchmarks import make_benchmark
from .chat import ChatBackend, ChatConfig
from .docker import DockerBackend, DockerConfig
from .faults import FaultConfig
from .harbor import HarborBackend, HarborConfig
from .injector import InjectOptions, Site
from .models import resolve_model
from .types import RunResult


@dataclass
class RunConfig:
    model: str
    benchmark: str
    benchmark_path: str
    harness: str = "opencode"  # only opencode is supported
    backend: str = "docker"  # harbor | docker | chat
    fault: FaultConfig = field(default_factory=FaultConfig)
    site: Site = Site.CONTEXT
    tools: list[str] = field(default_factory=list)
    log_path: str = "fault-inject.log.jsonl"
    limit: int | None = None
    tasks: list[str] = field(default_factory=list)
    dry_run: bool = False
    # harbor
    results_dir: str = "harbor_results"
    prebuilt: bool = False  # task image already ships the agent (skip install)
    memory_mb: int | None = None  # harbor: --override-memory-mb
    cpus: int | None = None  # harbor: --override-cpus
    # docker
    image_template: str | None = None  # e.g. ghcr.io/.../{name}:latest
    container_workdir: str = "/app"
    agent_setup: list[str] = field(default_factory=list)  # install agent in container
    build_image: bool = False  # prebuild task image with agent baked in
    # chat backend (host-side opencode)
    runs_dir: str = "runs"
    chat_agent: str = "chat"
    opencode_bin: str = "opencode"
    docker_opencode_bin: str | None = None
    docker_auth_file: str | None = None
    protect_tests: bool = True
    chat_timeout: int = 900
    inject_user: bool = True
    inject_tool: bool = True
    inject_assistant: bool = False
    clear_proxy_env: bool = True  # strip inherited proxy vars; no proxy path exists
    chat_retries: int = 3
    chat_retry_sleep: float = 5.0
    model_fallbacks: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.harness != "opencode":
            raise ValueError(f"unsupported harness {self.harness!r}; only 'opencode' is supported")
        if self.backend not in {"chat", "docker", "harbor"}:
            raise ValueError(f"unsupported backend {self.backend!r}")

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "RunConfig":
        fault = data.get("fault", {}) or {}
        return RunConfig(
            model=data["model"],
            model_fallbacks=list(data.get("model_fallbacks", []) or []),
            benchmark=data["benchmark"],
            benchmark_path=str(data["benchmark_path"]),
            harness=data.get("harness", "opencode"),
            backend=data.get("backend", "docker"),
            fault=FaultConfig(
                mode=fault.get("mode", "single"),
                prob=float(fault.get("prob", 0.0)),
                width=int(fault.get("width", 8)),
                seed=int(fault.get("seed", 1)),
            ),
            site=Site(data.get("site", "context")),
            tools=list(data.get("tools", []) or []),
            log_path=data.get("log_path", "fault-inject.log.jsonl"),
            limit=(int(data["limit"]) if data.get("limit") is not None else None),
            tasks=list(data.get("tasks", []) or []),
            dry_run=bool(data.get("dry_run", False)),
            results_dir=data.get("results_dir", "harbor_results"),
            prebuilt=bool(data.get("prebuilt", False)),
            memory_mb=(int(data["memory_mb"]) if data.get("memory_mb") else None),
            cpus=(int(data["cpus"]) if data.get("cpus") else None),
            image_template=data.get("image_template"),
            container_workdir=data.get("container_workdir", "/app"),
            agent_setup=list(data.get("agent_setup", []) or []),
            build_image=bool(data.get("build_image", False)),
            runs_dir=data.get("runs_dir", "runs"),
            chat_agent=data.get("chat_agent", "chat"),
            opencode_bin=data.get("opencode_bin", "opencode"),
            docker_opencode_bin=data.get("docker_opencode_bin"),
            docker_auth_file=data.get("docker_auth_file"),
            protect_tests=bool(data.get("protect_tests", True)),
            chat_timeout=int(data.get("chat_timeout", 900)),
            inject_user=bool(data.get("inject_user", True)),
            inject_tool=bool(data.get("inject_tool", True)),
            inject_assistant=bool(data.get("inject_assistant", False)),
            clear_proxy_env=bool(data.get("clear_proxy_env", True)),
            chat_retries=int(data.get("chat_retries", 3)),
            chat_retry_sleep=float(data.get("chat_retry_sleep", 5.0)),
        )


def _chat_needs_fallback(result: RunResult) -> bool:
    """Fallback only on transport/runtime failure, never on a wrong answer."""
    if result.error:
        return True
    return not (result.final_answer or "").strip()


def _archive_failed_run(run_dir: str | None, runs_dir: str) -> None:
    """Move a failed model attempt out of the top-level runs directory."""
    if not run_dir:
        return
    src = Path(run_dir)
    if not src.is_dir():
        return
    dest_root = Path(runs_dir) / "_failed"
    dest_root.mkdir(parents=True, exist_ok=True)
    dest = dest_root / src.name
    n = 1
    while dest.exists():
        dest = dest_root / f"{src.name}-{n}"
        n += 1
    shutil.move(str(src), str(dest))


def load_config(path: str | Path) -> RunConfig:
    p = Path(path)
    text = p.read_text()
    if p.suffix in (".yaml", ".yml"):
        if yaml is None:
            raise RuntimeError("PyYAML is required to read YAML configs")
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    return RunConfig.from_dict(data)


def run(config: RunConfig) -> list[RunResult]:
    model = resolve_model(config.model)
    bench_kwargs: dict = {}
    if config.benchmark == "nl2repo":
        if config.image_template:
            bench_kwargs["image_template"] = config.image_template
        bench_kwargs["container_workdir"] = config.container_workdir
    benchmark = make_benchmark(config.benchmark, config.benchmark_path, **bench_kwargs)
    inject = InjectOptions(
        fault=config.fault,
        site=config.site,
        tools=config.tools,
        log_path=config.log_path,
    )
    tasks = benchmark.tasks(None if config.tasks else config.limit)
    if config.tasks:
        selected = set(config.tasks)
        tasks = [t for t in tasks if t.name in selected]
        if config.limit is not None:
            tasks = tasks[: config.limit]

    if config.backend == "harbor":
        backend = HarborBackend(
            HarborConfig(
                prebuilt=config.prebuilt,
                inject_user=config.inject_user,
                inject_tool=config.inject_tool,
                inject_assistant=config.inject_assistant,
                results_dir=config.results_dir,
                memory_mb=config.memory_mb,
                cpus=config.cpus,
            )
        )
        return [backend.run(t, model, inject, dry_run=config.dry_run) for t in tasks]

    if config.backend == "docker":
        backend = DockerBackend(
            DockerConfig(
                harness=config.harness,
                agent_setup=config.agent_setup,
                build_image=config.build_image,
                runs_dir=config.runs_dir,
                opencode_bin=config.docker_opencode_bin,
                auth_file=config.docker_auth_file,
                inject_user=config.inject_user,
                inject_tool=config.inject_tool,
                inject_assistant=config.inject_assistant,
                protect_tests=config.protect_tests,
            )
        )
        return [backend.run(t, model, inject, dry_run=config.dry_run) for t in tasks]

    if config.backend == "chat":
        backend = ChatBackend(
            ChatConfig(
                opencode_bin=config.opencode_bin,
                runs_dir=config.runs_dir,
                timeout=config.chat_timeout,
                agent=config.chat_agent,
                inject_user=config.inject_user,
                inject_tool=config.inject_tool,
                inject_assistant=config.inject_assistant,
                clear_proxy_env=config.clear_proxy_env,
                max_retries=config.chat_retries,
                retry_sleep=config.chat_retry_sleep,
            )
        )
        models = [model]
        for name in config.model_fallbacks:
            spec = resolve_model(name)
            if all(spec.name != m.name for m in models):
                models.append(spec)

        results: list[RunResult] = []
        for task in tasks:
            attempts: list[dict[str, Any]] = []
            result = None
            for idx, candidate in enumerate(models):
                result = backend.run(task, candidate, inject, dry_run=config.dry_run)
                if config.dry_run or not _chat_needs_fallback(result):
                    break
                attempts.append(
                    {
                        "model": candidate.name,
                        "run_dir": result.run_dir,
                        "reason": result.error or "empty answer",
                    }
                )
                if idx + 1 < len(models):
                    _archive_failed_run(result.run_dir, config.runs_dir)
            assert result is not None
            if attempts and result.run_dir and not config.dry_run:
                (Path(result.run_dir) / "fallback.json").write_text(
                    json.dumps(
                        {
                            "primary_model": models[0].name,
                            "used_model": result.model or models[-1].name,
                            "attempts": attempts,
                        },
                        indent=2,
                    )
                )
            results.append(result)
            if result.run_dir and not config.dry_run:
                try:
                    from .report import generate_report

                    generate_report(result.run_dir)
                except Exception:
                    pass
        if not config.dry_run:
            try:
                from .analysis import analyze_runs

                analyze_runs(config.runs_dir)
            except Exception:
                pass
        return results

    raise ValueError(
        f"unknown backend {config.backend!r}; use 'harbor', 'docker', or 'chat'"
    )
