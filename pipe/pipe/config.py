"""Run configuration (YAML/JSON) and the top-level run pipeline.

Everything runs in containers. ``backend`` picks the container runner and
``harness`` picks the agent inside it:

    backend: harbor | docker      harness: opencode | codex
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from .benchmarks import make_benchmark
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
    harness: str = "opencode"  # agent inside the container: opencode | codex
    backend: str = "docker"  # container runner: harbor | docker
    fault: FaultConfig = field(default_factory=FaultConfig)
    site: Site = Site.CONTEXT
    tools: list[str] = field(default_factory=list)
    log_path: str = "fault-inject.log.jsonl"
    limit: int | None = None
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

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "RunConfig":
        fault = data.get("fault", {}) or {}
        return RunConfig(
            model=data["model"],
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
            dry_run=bool(data.get("dry_run", False)),
            results_dir=data.get("results_dir", "harbor_results"),
            prebuilt=bool(data.get("prebuilt", False)),
            memory_mb=(int(data["memory_mb"]) if data.get("memory_mb") else None),
            cpus=(int(data["cpus"]) if data.get("cpus") else None),
            image_template=data.get("image_template"),
            container_workdir=data.get("container_workdir", "/app"),
            agent_setup=list(data.get("agent_setup", []) or []),
            build_image=bool(data.get("build_image", False)),
        )


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
    tasks = benchmark.tasks(config.limit)

    if config.backend == "harbor":
        backend = HarborBackend(
            HarborConfig(
                harness=config.harness,
                prebuilt=config.prebuilt,
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
            )
        )
        return [backend.run(t, model, inject, dry_run=config.dry_run) for t in tasks]

    raise ValueError(f"unknown backend {config.backend!r}; use 'harbor' or 'docker'")
