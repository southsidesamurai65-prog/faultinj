import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipe.analysis import label_injection  # noqa: E402
from pipe.report import generate_report  # noqa: E402


def _res(reward=1.0, final="18", gold="18"):
    return {"reward": reward, "final_answer": final, "gold": gold}


def test_recovered_silently():
    rec = {"before": "value 42", "after": "value \ufffd2", "n_flips": 1}
    out = label_injection(rec, _res(), "the answer is 42")
    assert out["detected"] is False
    assert out["outcome"] == "recovered_silently"


def test_silent_error():
    rec = {"before": "value 42", "after": "value 99", "n_flips": 1}
    out = label_injection(rec, _res(0.0, "99"), "the answer is 99")
    assert out["outcome"] == "silent_error"
    assert out["uses_corrupted_value"] is True


def test_explicit_detection():
    rec = {"before": "value 42", "after": "value \ufffd2", "n_flips": 1}
    out = label_injection(rec, _res(0.0, ""), "The output looks garbled; retry")
    assert out["detected"] is True
    assert out["action"] in ("mention", "mention_and_retry", "retry_or_verify")


def test_tool_retry_is_detected():
    rec = {"before": "42", "after": "99", "n_flips": 1, "tool": "bash", "ts": "2026-01-01T00:00:00Z"}
    calls = [
        {"tool": "bash", "args": {"command": "cat f"}, "ts": 1767225601000},
        {"tool": "bash", "args": {"command": "cat f"}, "ts": 1767225602000},
    ]
    out = label_injection(rec, _res(0.0, "99"), "checking", calls)
    assert out["detected"] is True
    assert out["action"] in ("retry_same_tool", "mention_and_retry")


def test_report(tmp_path):
    (tmp_path / "result.json").write_text(json.dumps({"task": "t", "reward": 0, "final_answer": "99"}))
    (tmp_path / "task.json").write_text(json.dumps({"instruction": "q", "model": "m"}))
    (tmp_path / "behavior.jsonl").write_text(
        json.dumps({"where": "user_message", "before": "42", "after": "99", "n_flips": 1}) + "\n"
    )
    out = generate_report(tmp_path)
    text = out.read_text()
    assert "before" in text and "after" in text and "42" in text and "99" in text
