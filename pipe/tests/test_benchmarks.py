import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipe import NL2Repo, TerminalBench, run_commands  # noqa: E402


def test_terminal_bench_loader(tmp_path):
    task_dir = tmp_path / "my-task"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "task.toml").write_text("[task]\nname = 'x'\n")
    (task_dir / "instruction.md").write_text("do the task")
    tb = TerminalBench(tmp_path)
    tasks = tb.tasks()
    assert len(tasks) == 1
    assert tasks[0].name == "my-task" and tasks[0].instruction == "do the task"


def test_nl2repo_loader(tmp_path):
    t = tmp_path / "six"
    t.mkdir()
    (t / "start.md").write_text("build six")
    (t / "test_commands.json").write_text(json.dumps(["echo hi"]))
    tasks = NL2Repo(tmp_path).tasks()
    assert len(tasks) == 1
    assert tasks[0].name == "six" and tasks[0].instruction == "build six"


def test_run_commands_parses_pytest_rate(tmp_path):
    # a command whose output contains pytest-style counts
    cmd = "python3 -c \"print('3 passed, 1 failed in 0.1s')\""
    result = run_commands(tmp_path, [cmd])
    assert result.partial == "3/4"
    assert abs(result.reward - 0.75) < 1e-9


def test_run_commands_binary_fallback(tmp_path):
    assert run_commands(tmp_path, ["true"]).reward == 1.0
    assert run_commands(tmp_path, ["false"]).reward == 0.0
