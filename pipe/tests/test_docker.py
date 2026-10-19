import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipe import (  # noqa: E402
    DockerBackend,
    DockerConfig,
    FaultConfig,
    InjectOptions,
    Site,
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


def test_codex_install_and_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPE_CODEX_BASE_URL", "https://codex/api")
    monkeypatch.setenv("PIPE_CODEX_API_KEY", "sk-codex")
    backend = DockerBackend(DockerConfig(harness="codex"))
    model = resolve_model("gpt-6-luna")
    inject = InjectOptions(fault=FaultConfig(), site=Site.CONTEXT)

    install = backend.install_command(model, inject)
    assert "fault_shim.py" in install and "/tmp/fault-bin" in install
    assert "profile.d" in install  # multi-channel PATH activation
    agent = backend.agent_command(_task(tmp_path), model)
    assert "codex exec --model gpt-6-luna" in agent
    env = backend.agent_env(model)
    assert env["OPENAI_API_KEY"] == "sk-codex" and env["OPENAI_BASE_URL"] == "https://codex/api"
    assert env["PATH"].startswith("/tmp/fault-bin:")


def test_proxy_env():
    assert DockerBackend(DockerConfig()).proxy_env() == {}
    backend = DockerBackend(DockerConfig(proxy="http://172.17.0.1:19829"))
    env = backend.proxy_env()
    assert env["http_proxy"] == env["https_proxy"] == "http://172.17.0.1:19829"
    assert "no_proxy" in env


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
