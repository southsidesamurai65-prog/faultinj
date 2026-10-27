"""Rule-based behaviour analysis for completed runs.

Inputs (written by the plugin / chat backend):
  result.json       task outcome + final answer
  injections.jsonl  one record per injected string (before/after)
  agent.jsonl       opencode JSON events

Outputs:
  behavior.jsonl    one labelled record per injection
  summary.json      aggregate counts

The rules are intentionally conservative.  They are a first pass for triage;
a small hand-labelled sample should be used to calibrate them later.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

_ANOMALY_RE = re.compile(
    r"(corrupt|garbled|gibberish|malformed|invalid\b|typo|unexpected|doesn['’]t make sense|"
    r"does not make sense|looks wrong|looks off|mismatch|contradict|"
    r"乱码|损坏|异常|不对|不一致|矛盾|看不懂|奇怪)",
    re.I,
)
_RETRY_RE = re.compile(
    r"(retry|try again|re-?read|re-?run|rerun|check again|"
    r"重新|再读|重读|重试|再跑|检查一下)",
    re.I,
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def read_agent_events(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path)


def read_agent_text(path: Path) -> str:
    return "".join(
        part["text"]
        for ev in read_jsonl(path)
        if ev.get("type") == "text"
        for part in [ev.get("part") or {}]
        if isinstance(part.get("text"), str)
    )


def _tool_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for ev in events:
        part = ev.get("part") or {}
        if ev.get("type") not in ("tool", "tool_call") and part.get("type") != "tool":
            continue
        state = part.get("state") or {}
        args = state.get("input") or part.get("input") or {}
        calls.append(
            {
                "tool": part.get("tool") or part.get("name") or "",
                "args": args,
                "status": state.get("status"),
                "output": state.get("output"),
                "ts": ev.get("timestamp"),
            }
        )
    return calls


def _command_text(args: Any) -> str:
    if isinstance(args, dict):
        return " ".join(str(v) for k, v in args.items() if k in ("command", "path", "filePath", "file"))
    return str(args)


def _tool_action(rec: dict[str, Any], calls: list[dict[str, Any]]) -> str:
    if not calls:
        return "none"
    tool = str(rec.get("tool") or "")
    # Only calls after the injection are relevant.  Plugin timestamps are ISO,
    # event timestamps are epoch milliseconds; fall back to all calls.
    after = calls
    try:
        from datetime import datetime

        t = datetime.fromisoformat(str(rec.get("ts")).replace("Z", "+00:00")).timestamp() * 1000
        after = [c for c in calls if not c.get("ts") or float(c["ts"]) >= t] or calls
    except Exception:
        pass

    same = [c for c in after if tool and c.get("tool") == tool]
    if len(same) >= 2:
        return "retry_same_tool"
    for c in after:
        cmd = _command_text(c.get("args")).lower()
        if any(x in cmd for x in ("xxd", "sha256sum", "md5sum", "git diff", "stat ", "file ", "wc ")):
            return "verify_with_alternate_tool"
        if c.get("tool") in ("read", "grep", "glob") or any(
            x in cmd for x in ("cat ", "sed ", "grep ", "head ", "tail ", "less ", "awk ")
        ):
            return "reread_or_search"
    return "none"


def _numbers(text: str) -> set[str]:
    return {m.replace(",", "") for m in re.findall(r"-?\d[\d,]*(?:\.\d+)?", text)}


def _corrupted_tokens(before: str, after: str) -> set[str]:
    """Distinctive tokens that appear only after the fault."""
    b = set(re.findall(r"[A-Za-z0-9_.:/-]{3,}", before))
    a = set(re.findall(r"[A-Za-z0-9_.:/-]{3,}", after))
    new = a - b
    # Prefer tokens that actually contain a replacement char or look changed.
    return {t for t in new if "\ufffd" in t or any(c.isdigit() for c in t)} | (
        new if len(new) <= 8 else set()
    )


def _mentions_token(text: str, token: str) -> bool:
    """Match a corrupted token without accepting it inside a normal word.

    Faults split words (``available`` -> ``availab<replacement>``), so a plain
    substring check would mark the intact word as "used corrupted value".
    """
    if not token:
        return False
    return re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", text
    ) is not None


def label_injection(
    rec: dict[str, Any],
    result: dict[str, Any],
    agent_text: str,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    before = str(rec.get("before", ""))
    after = str(rec.get("after", ""))
    final = str(result.get("final_answer") or "")
    gold = str(result.get("gold") or "")
    reward = float(result.get("reward") or 0.0)

    anomaly = bool(_ANOMALY_RE.search(agent_text))
    retry = bool(_RETRY_RE.search(agent_text))
    tool_action = _tool_action(rec, tool_calls or [])
    new_tokens = _corrupted_tokens(before, after)
    uses_corrupted = any(_mentions_token(final, t) for t in new_tokens)
    corrupted_numbers = _numbers(after) - _numbers(before)
    uses_corrupted_number = any(_mentions_token(final, n) for n in corrupted_numbers)
    has_replacement = "\ufffd" in after
    trivial = before == after

    # Coding agents read/search/run bash constantly.  Only explicit anomaly
    # text, retry language, or a deliberate alternate verification counts.
    detection_tool = tool_action == "verify_with_alternate_tool"
    if trivial:
        detected = False
        outcome = "no_injection"
    elif anomaly or retry or detection_tool:
        detected = True
        outcome = "recovered" if reward >= 1.0 else "detected_but_failed"
    else:
        detected = False
        if reward >= 1.0:
            outcome = "recovered_silently"
        elif uses_corrupted or uses_corrupted_number:
            outcome = "silent_error"
        else:
            outcome = "failed_unattributed"

    if anomaly and (retry or tool_action != "none"):
        action = "mention_and_retry"
    elif anomaly:
        action = "mention"
    elif retry:
        action = "retry_or_verify"
    elif tool_action != "none":
        action = tool_action
    elif uses_corrupted or uses_corrupted_number:
        action = "used_corrupted_value"
    else:
        action = "none"

    return {
        "event_id": rec.get("event_id"),
        "run_id": rec.get("run_id"),
        "task_id": rec.get("task_id"),
        "site": rec.get("site"),
        "where": rec.get("where"),
        "tool": rec.get("tool"),
        "field": rec.get("field"),
        "mode": rec.get("mode"),
        "prob": rec.get("prob"),
        "seed": rec.get("seed"),
        "n_flips": rec.get("n_flips"),
        "n_bytes": rec.get("n_bytes"),
        "has_replacement": has_replacement,
        "detected": detected,
        "detection_signal": (
            "explicit"
            if anomaly
            else ("retry" if retry else ("tool_action" if detection_tool else "none"))
        ),
        "action": action,
        "tool_action": tool_action,
        "tool_calls_after": len(tool_calls or []),
        "outcome": outcome,
        "reward": reward,
        "gold": gold,
        "final_answer": final,
        "uses_corrupted_value": uses_corrupted or uses_corrupted_number,
        "before": before,
        "after": after,
    }


def analyze_run(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir)
    result: dict[str, Any] = {}
    if (root / "result.json").is_file():
        result = json.loads((root / "result.json").read_text())
    injections = read_jsonl(root / "injections.jsonl")
    events = read_agent_events(root / "agent.jsonl")
    agent_text = "".join(
        part["text"]
        for ev in events
        if ev.get("type") == "text"
        for part in [ev.get("part") or {}]
        if isinstance(part.get("text"), str)
    )
    calls = _tool_calls(events)

    behaviours = [label_injection(rec, result, agent_text, calls) for rec in injections]
    with (root / "behavior.jsonl").open("w") as fh:
        for row in behaviours:
            fh.write(json.dumps(row) + "\n")

    outcomes = Counter(r["outcome"] for r in behaviours)
    actions = Counter(r["action"] for r in behaviours)
    summary = {
        "task": result.get("task"),
        "reward": result.get("reward"),
        "final_answer": result.get("final_answer"),
        "gold": result.get("gold"),
        "n_injections": len(behaviours),
        "n_flips": sum(int(r.get("n_flips") or 0) for r in behaviours),
        "detected": sum(bool(r["detected"]) for r in behaviours),
        "silent_error": outcomes.get("silent_error", 0),
        "recovered": outcomes.get("recovered", 0) + outcomes.get("recovered_silently", 0),
        "detected_but_failed": outcomes.get("detected_but_failed", 0),
        "failed_unattributed": outcomes.get("failed_unattributed", 0),
        "outcomes": dict(outcomes),
        "actions": dict(actions),
    }
    (root / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def analyze_runs(runs_dir: str | Path) -> dict[str, Any]:
    """Analyze every run directory under ``runs_dir`` and write a batch summary."""
    root = Path(runs_dir)
    rows: list[dict[str, Any]] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if not (d / "result.json").is_file():
            continue
        rows.append(analyze_run(d))
    totals = {
        "runs": len(rows),
        "tasks": sum(1 for r in rows if r.get("task")),
        "injections": sum(int(r.get("n_injections") or 0) for r in rows),
        "detected": sum(int(r.get("detected") or 0) for r in rows),
        "silent_error": sum(int(r.get("silent_error") or 0) for r in rows),
        "recovered": sum(int(r.get("recovered") or 0) for r in rows),
        "success": sum(1 for r in rows if float(r.get("reward") or 0) >= 1.0),
    }
    out = {"totals": totals, "runs": rows}
    (root / "summary.json").write_text(json.dumps(out, indent=2))
    return out


def compare_runs(runs_dir: str | Path) -> dict[str, Any]:
    """Pair baseline (prob=0) and injected runs by task.

    Writes ``compare.json`` and ``compare.csv`` next to the run directories.
    """
    import csv

    root = Path(runs_dir)
    records: list[dict[str, Any]] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if not (d / "result.json").is_file():
            continue
        result = json.loads((d / "result.json").read_text())
        task = {}
        if (d / "task.json").is_file():
            task = json.loads((d / "task.json").read_text())
        fault = result.get("fault") or task.get("fault") or {}
        summary = {}
        if (d / "summary.json").is_file():
            summary = json.loads((d / "summary.json").read_text())
        records.append(
            {
                "run_dir": d.name,
                "task": result.get("task"),
                "model": result.get("model") or task.get("model"),
                "prob": float(fault.get("prob") or 0.0),
                "seed": fault.get("seed"),
                "reward": float(result.get("reward") or 0.0),
                "final_answer": result.get("final_answer"),
                "gold": result.get("gold") or task.get("answer"),
                "injections": int(summary.get("n_injections") or result.get("injections") or 0),
                "detected": int(summary.get("detected") or 0),
                "silent_error": int(summary.get("silent_error") or 0),
                "site": result.get("site") or task.get("site"),
                "inject_user": result.get("inject_user"),
                "inject_tool": result.get("inject_tool"),
                "inject_assistant": result.get("inject_assistant"),
            }
        )

    by_task: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in records:
        by_task.setdefault((str(r["task"]), str(r.get("model") or "")), []).append(r)

    rows: list[dict[str, Any]] = []
    for (task, model), runs in sorted(by_task.items()):
        base = next((r for r in runs if r["prob"] == 0.0), None)
        for r in runs:
            if r["prob"] == 0.0:
                continue
            rows.append(
                {
                    "task": task,
                    "model": model,
                    "baseline_reward": base["reward"] if base else None,
                    "fault_reward": r["reward"],
                    "delta": (r["reward"] - base["reward"]) if base else None,
                    "prob": r["prob"],
                    "seed": r["seed"],
                    "injections": r["injections"],
                    "detected": r["detected"],
                    "silent_error": r["silent_error"],
                    "site": r.get("site"),
                    "inject_user": r.get("inject_user"),
                    "inject_tool": r.get("inject_tool"),
                    "inject_assistant": r.get("inject_assistant"),
                    "final_answer": r["final_answer"],
                    "gold": r["gold"],
                    "run_dir": r["run_dir"],
                }
            )

    out = {"runs_dir": str(root), "rows": rows}
    (root / "compare.json").write_text(json.dumps(out, indent=2))
    with (root / "compare.csv").open("w", newline="") as fh:
        if rows:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return out
