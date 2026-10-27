import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipe import (  # noqa: E402
    FaultConfig,
    HarborBackend,
    HarborConfig,
    InjectOptions,
    Site,
    Task,
    resolve_model,
)
from pipe.types import EvalResult  # noqa: E402


def _task(tmp_path: Path) -> Task:
    return Task(
        name="regex-log",
        instruction="x",
        workdir=tmp_path,
        evaluator=lambda wd: EvalResult(1.0),
    )


def test_harbor_build_command(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPE_OPENCODE_BASE_URL", "https://go:8080/v1")
    monkeypatch.setenv("PIPE_OPENCODE_API_KEY", "sk-open")
    backend = HarborBackend(HarborConfig(prebuilt=True, results_dir="res", attempts=2))
    model = resolve_model("deepseek-v4.1-flash")
    inject = InjectOptions(fault=FaultConfig(mode="burst", prob=0.01, width=4), site=Site.CONTEXT)
    cmd = backend.build_command(_task(tmp_path), model, inject)

    assert cmd[:2] == ["harbor", "run"]
    assert "-m" in cmd and cmd[cmd.index("-m") + 1] == "pipe/deepseek-v4.1-flash"
    assert "-a" in cmd and cmd[cmd.index("-a") + 1].endswith("FaultOpenCode")
    assert "fault_prebuilt=true" in cmd
    assert "fault_width=4" in cmd
    assert "fault_mode=burst" in cmd
    assert "inject_user=false" in cmd
    assert "inject_tool=true" in cmd
    assert str(tmp_path) in cmd  # -p task dir

    cfg = backend.opencode_config(model)
    opts = cfg["provider"]["pipe"]["options"]
    assert opts["baseURL"] == "https://go:8080/v1" and opts["apiKey"] == "sk-open"
    assert set(cfg["provider"]["pipe"]["models"]) == {
        "deepseek-v4.1-flash",
        "mimo-v2.6-flash-free",
        "mimo-v2.6-flash",
    }


def test_harbor_env_has_pythonpath(monkeypatch):
    backend = HarborBackend()
    env = backend.build_env()
    assert "PYTHONPATH" in env and str(Path(env["PYTHONPATH"].split(":")[0])).endswith("pipe")
