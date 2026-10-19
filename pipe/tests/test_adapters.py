import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipe import RunConfig, Site, load_config, resolve_connection, resolve_model  # noqa: E402


def test_model_registry():
    assert resolve_model("deepseek-v4.1-flash").wire_model == "deepseek-v4.1-flash"
    assert resolve_model("gpt-6-luna").wire_model == "gpt-6-luna"


def test_connections_are_per_harness(monkeypatch):
    monkeypatch.setenv("PIPE_OPENCODE_BASE_URL", "https://go:8080/v1")
    monkeypatch.setenv("PIPE_OPENCODE_API_KEY", "sk-open")
    monkeypatch.setenv("PIPE_CODEX_BASE_URL", "https://codex/api")
    monkeypatch.setenv("PIPE_CODEX_API_KEY", "sk-codex")

    _, oc = resolve_connection("opencode")
    assert oc == {"base_url": "https://go:8080/v1", "api_key": "sk-open"}
    _, cx = resolve_connection("codex")
    assert cx == {"base_url": "https://codex/api", "api_key": "sk-codex"}


def test_load_config(tmp_path):
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        "backend: harbor\n"
        "harness: codex\n"
        "model: gpt-6-luna\n"
        "benchmark: terminal-bench\n"
        "benchmark_path: /tmp/tasks\n"
        "site: tool\n"
        "fault:\n  mode: burst\n  prob: 0.02\n  width: 4\n  seed: 9\n"
        "limit: 2\ndry_run: true\nprebuilt: true\n"
    )
    cfg = load_config(cfg_file)
    assert isinstance(cfg, RunConfig)
    assert cfg.backend == "harbor" and cfg.harness == "codex" and cfg.model == "gpt-6-luna"
    assert cfg.site is Site.TOOL
    assert cfg.fault.mode == "burst" and cfg.fault.width == 4 and cfg.fault.seed == 9
    assert cfg.limit == 2 and cfg.dry_run is True and cfg.prebuilt is True


def test_config_defaults():
    cfg = RunConfig(model="gpt-6-luna", benchmark="nl2repo", benchmark_path="/x")
    assert cfg.backend == "docker" and cfg.harness == "opencode"
