import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipe import (  # noqa: E402
    DockerBackend,
    DockerConfig,
    FaultConfig,
    InjectOptions,
    Task,
    resolve_model,
)
from pipe.types import EvalResult  # noqa: E402


def _task(tmp_path: Path) -> Task:
    return Task(
        name="six",
        instruction="build six",
        workdir=tmp_path,
        evaluator=lambda wd: EvalResult(1.0),
        image="ghcr.io/example/nl2repobench/six:latest",
        container_workdir="/app",
    )


def test_build_create(tmp_path):
    backend = DockerBackend(DockerConfig(harness="opencode"))
    task = _task(tmp_path)
    cmd = backend.build_create(task, "c1")
    assert cmd[:4] == ["docker", "run", "-d", "--name"]
    assert cmd[cmd.index("-w") + 1] == "/app"
    assert "ghcr.io/example/nl2repobench/six:latest" in cmd
    assert cmd[-2:] == ["sleep", "infinity"]


def test_opencode_install_and_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPE_OPENCODE_BASE_URL", "https://go/v1")
    monkeypatch.setenv("PIPE_OPENCODE_API_KEY", "sk-open")
    backend = DockerBackend(DockerConfig(harness="opencode"))
    model = resolve_model("deepseek-v4.1-flash")
    inject = InjectOptions(fault=FaultConfig(mode="burst", prob=0.01, width=4))

    install = backend.install_command(model, inject)
    assert "fault-inject.ts" in install and "opencode.json" in install
    agent = backend.agent_command(_task(tmp_path), model)
    assert "opencode run --model pipe/deepseek-v4.1-flash" in agent
    assert backend.agent_env(model)["OPENCODE_CONFIG"].endswith("opencode.json")


def test_build_exec_env_flags(tmp_path):
    backend = DockerBackend()
    cmd = backend.build_exec("c1", "echo hi", {"A": "1", "B": "2"})
    assert cmd[:2] == ["docker", "exec"]
    assert "-e" in cmd and "A=1" in cmd and "B=2" in cmd
    assert cmd[-3:] == ["bash", "-lc", "echo hi"]


def test_dry_run_returns_plan(tmp_path):
    backend = DockerBackend(DockerConfig(harness="opencode"))
    task = _task(tmp_path)
    result = backend.run(task, resolve_model("deepseek-v4.1-flash"), InjectOptions(fault=FaultConfig()), dry_run=True)
    assert result.detail == "dry-run" and "docker run" in result.error


def test_go_provider_assets_and_command(tmp_path, monkeypatch):
    binary = tmp_path / "opencode"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    auth = tmp_path / "auth.json"
    auth.write_text('{"opencode-go":{"type":"api","key":"x"}}')
    model = resolve_model("deepseek-v4.1-flash-go")
    backend = DockerBackend(
        DockerConfig(
            harness="opencode",
            opencode_bin=str(binary),
            auth_file=str(auth),
            inject_tool=False,
        )
    )
    backend._assets = backend._opencode_assets(model)
    assert backend._assets
    cmd = backend.build_create(_task(tmp_path), "c1")
    joined = " ".join(cmd)
    assert ":/usr/local/bin/opencode:ro" in joined
    assert ":/root/.local/share/opencode/auth.json:ro" in joined
    agent = backend.agent_command(_task(tmp_path), model)
    assert "opencode-go/deepseek-v4.1-flash" in agent
    assert "--auto" in agent and "--agent chat" in agent
    install = backend.install_command(
        model,
        InjectOptions(fault=FaultConfig(mode="burst", prob=0.01)),
        run_id="r1",
        task_id="t1",
    )
    assert "fault-inject.ts" in install and "opencode.json" in install


def test_go_assets_missing(tmp_path):
    backend = DockerBackend(
        DockerConfig(harness="opencode", auth_file=str(tmp_path / "missing.json"))
    )
    try:
        backend._opencode_assets(resolve_model("deepseek-v4.1-flash-go"))
    except RuntimeError as exc:
        assert "opencode binary" in str(exc) or "auth file" in str(exc)
    else:
        raise AssertionError("missing go assets should raise")
