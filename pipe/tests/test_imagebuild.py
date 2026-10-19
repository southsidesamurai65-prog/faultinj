import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipe import DockerBackend, DockerConfig, FaultConfig, InjectOptions, Task, build_image, default_tag  # noqa: E402
from pipe.imagebuild import dockerfile_for  # noqa: E402
from pipe.types import EvalResult  # noqa: E402


def test_default_tag():
    assert default_tag("alexgshaw/regex-log:20251031", "opencode") == (
        "pipe-alexgshaw-regex-log-20251031-opencode:latest"
    )


def test_dockerfile_opencode():
    df = dockerfile_for("ubuntu:24.04", "opencode")
    assert df.startswith("FROM ubuntu:24.04")
    assert "nodejs npm" in df
    assert "npm i -g opencode-ai@latest" in df
    assert "opencode --version" in df
    assert "pytest==8.4.1" in df


def test_dockerfile_codex_no_pytest():
    df = dockerfile_for("python:3.11-slim", "codex", with_pytest=False)
    assert "@openai/codex" in df and "codex --version" in df
    assert "pytest==" not in df


def test_docker_backend_uses_built_tag(tmp_path):
    task = Task(
        name="six",
        instruction="x",
        workdir=tmp_path,
        evaluator=lambda wd: EvalResult(1.0),
        image="ghcr.io/x/nl2repobench/six:latest",
    )
    backend = DockerBackend(DockerConfig(harness="opencode", build_image=True))
    # dry-run must not build, but must plan the derived image tag
    tag = backend.resolved_image(task, build=False)
    assert tag == default_tag(task.image, "opencode")
    result = backend.run(task, __import__("pipe").resolve_model("deepseek-v4.1-flash"), InjectOptions(fault=FaultConfig()), dry_run=True)
    assert tag in result.error
