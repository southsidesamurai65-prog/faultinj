#!/usr/bin/env python3
"""Dependency-light checks (pytest is not installed in this environment)."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipe.benchmarks import (
    CodeQA,
    KnowledgeQA,
    LongContextQA,
    LongContextSelfCheckQA,
    MathQA,
    MathSelfCheckQA,
    extract_final_answer,
    math_correct,
)
from pipe.analysis import label_injection
from pipe.chat import ChatBackend, ChatConfig
from pipe.runio import format_prob, run_dir_name, scheme_sites, session_id
from pipe.config import RunConfig
from pipe.faults import FaultConfig
from pipe.injector import InjectOptions, Site
from pipe.types import Task
from pipe.types import EvalResult
from pipe.models import resolve_model
from pipe.report import generate_report
from pipe.gui import build_command


def main() -> None:
    base = ROOT.parent / "data" / "pilot"
    for cls, name, n in (
        (MathQA, "math.jsonl", 5),
        (MathQA, "math30.jsonl", 30),
        (KnowledgeQA, "knowledge.jsonl", 5),
        (LongContextQA, "longcontext.jsonl", 3),
    ):
        tasks = cls(base / name).tasks()
        assert len(tasks) == n, (cls, len(tasks))
    code_tasks = CodeQA(ROOT.parent / "data" / "code" / "humaneval3.jsonl").tasks()
    assert len(code_tasks) == 3
    selfcheck_tasks = MathSelfCheckQA(base / "math30.jsonl").tasks()
    assert len(selfcheck_tasks) == 30 and selfcheck_tasks[0].metadata["followup"]
    long_self = LongContextSelfCheckQA(base / "longcontext.jsonl").tasks()
    assert len(long_self) == 3 and long_self[0].metadata["followup"]
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "solution.py").write_text(
            "def truncate_number(number: float) -> float:\n    return number % 1\n"
        )
        assert code_tasks[0].evaluator(Path(td)).reward == 1.0
    assert extract_final_answer("Answer: 18") == "18"
    assert math_correct("18.0", "18")

    split_word = label_injection(
        {"before": "tools are available", "after": "tools are availab\ufffd\ufffd\ufffd", "n_flips": 64},
        {"reward": 0, "final_answer": "clipboard tools are available", "gold": None},
        "done",
    )
    assert split_word["uses_corrupted_value"] is False
    assert split_word["outcome"] == "failed_unattributed"

    rec = {"before": "value 42", "after": "value 99", "n_flips": 1}
    out = label_injection(rec, {"reward": 0, "final_answer": "99", "gold": "18"}, "answer is 99")
    assert out["outcome"] == "silent_error"
    reread = label_injection(
        rec,
        {"reward": 1, "final_answer": "42", "gold": "42"},
        "done",
        [{"tool": "read", "args": {"filePath": "solution.py"}}],
    )
    assert reread["detected"] is False
    normal_error = label_injection(
        {"before": "x", "after": "y", "n_flips": 1},
        {"reward": 1, "final_answer": "InvalidLineError", "gold": "ok"},
        "Added InvalidLineError handling",
    )
    assert normal_error["detected"] is False

    backend = ChatBackend(ChatConfig())
    cfg = backend.opencode_config(MathQA(base / "math.jsonl").tasks()[0], resolve_model("mimo-v2.6-flash-free"))
    assert cfg["plugin"] and cfg["agent"]["chat"]["model"] == "opencode/mimo-v2.6-flash-free"
    fault_cfg = backend.fault_config(
        InjectOptions(fault=FaultConfig(prob=0.1)), run_id="r", task_id="t", log_path=base / "x"
    )
    assert fault_cfg["injectTool"] is True and fault_cfg["injectAssistant"] is False
    assert session_id('{"type":"step_start","sessionID":"ses_x"}') == "ses_x"
    assert scheme_sites("context", True, False, False) == ("A", "user")
    assert scheme_sites("context", False, True, False) == ("B", "tool_result")
    assert scheme_sites("context", True, True, False) == ("C", "user+tool_result")
    assert scheme_sites("tool", False, False, False) == ("D", "tool_arg")
    assert scheme_sites("context", False, False, True) == ("E", "assistant")
    assert scheme_sites("context", False, False, False) == ("baseline", "none")
    assert format_prob(0.00001) == "0.00001"

    class _Model:
        name = "deepseek-v4.1-flash-go"

    class _Task:
        name = "HumanEval/3"

    class _Inject:
        fault = FaultConfig(prob=0.001, mode="single", width=8, seed=1)

    dirname = run_dir_name(_Task(), _Model(), _Inject(), scheme="A", sites="user")
    assert dirname.endswith("_HumanEval-3_A_user_p0.001_single_s1"), dirname
    class _BurstInject:
        fault = FaultConfig(prob=0.001, mode="burst", width=8, seed=1)
    dirname = run_dir_name(_Task(), _Model(), _BurstInject(), scheme="B", sites="tool_result")
    assert dirname.endswith("_HumanEval-3_B_tool_result_p0.001_burst8_s1"), dirname
    cmd = backend.command(
        MathQA(base / "math.jsonl").tasks()[0],
        resolve_model("mimo-v2.6-flash"),
        base,
        prompt="review",
        session="ses_x",
    )
    assert "--session" in cmd and cmd[cmd.index("--session") + 1] == "ses_x"
    assert cmd[-1] == "review"
    no_tool_backend = ChatBackend(ChatConfig(inject_tool=False))
    no_tool_cfg = no_tool_backend.fault_config(
        InjectOptions(fault=FaultConfig(prob=0.1)), run_id="r", task_id="t", log_path=base / "x"
    )
    assert no_tool_cfg["injectTool"] is False
    chain = RunConfig.from_dict(
        {"model": "mimo-v2.6-flash-free", "model_fallbacks": ["mimo-v2.6-flash"], "benchmark": "math", "benchmark_path": "x"}
    )
    assert chain.model_fallbacks == ["mimo-v2.6-flash"]
    gui_cmd = build_command(
        "configs/math30/math30-deepseek-v4.1-flash-go-opencode/E_assistant_p0.005_single_s1.yaml",
        {
            "tasks": "gsm8k-0",
            "prob": "0.001",
            "inject_user": "off",
            "inject_tool": "off",
            "inject_assistant": "on",
            "dry_run": True,
        },
    )
    assert "--no-inject-user" in gui_cmd and "--inject-assistant" in gui_cmd
    assert "gsm8k-0" in gui_cmd and "--dry-run" in gui_cmd

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "result.json").write_text(json.dumps({"task": "t", "reward": 1, "final_answer": "18"}))
        (root / "task.json").write_text(json.dumps({"instruction": "q", "model": "m"}))
        (root / "behavior.jsonl").write_text(json.dumps({"where": "user_message", "before": "x", "after": "y"}) + "\n")
        assert generate_report(root).is_file()

    # End-to-end chat backend with a fake opencode binary (no network).
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        fake = root / "fake-opencode"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os\n"
            "assert os.environ.get('FAULT_CONFIG') and os.path.isfile(os.environ['FAULT_CONFIG'])\n"
            "print(json.dumps({'type':'text','part':{'text':'Answer: 18'}}))\n"
        )
        fake.chmod(0o755)
        task = Task(
            name="fake-math",
            instruction="q",
            workdir=root,
            evaluator=lambda wd: EvalResult(1.0 if (wd / "answer.txt").read_text().strip() == "Answer: 18" else 0.0),
            answer="18",
        )
        backend = ChatBackend(
            ChatConfig(opencode_bin=str(fake), runs_dir=str(root / "runs"), timeout=10, max_retries=0)
        )
        out = backend.run(task, resolve_model("mimo-v2.6-flash-free"), InjectOptions(fault=FaultConfig(prob=0)))
        assert out.reward == 1.0 and out.run_dir and Path(out.run_dir).is_dir()

    print("selfcheck OK")


if __name__ == "__main__":
    main()
