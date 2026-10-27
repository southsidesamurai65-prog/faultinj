#!/usr/bin/env python3
"""Import completed Harbor Terminal-Bench trials into ``runs/``.

Harbor stores trials as ``<job>/<task>__<trial>/``.  This script copies the
useful artifacts into the normal pipe run layout, enriches the fault log with
run ids, labels every injection with :mod:`pipe.analysis`, and writes the
per-run ``report.html``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
_SECRET_RE = re.compile(r"oc_sk_[A-Za-z0-9_]+")
sys.path.insert(0, str(ROOT))

from pipe.analysis import label_injection  # noqa: E402
from pipe.report import generate_report  # noqa: E402

SCHEMES: dict[str, dict[str, Any]] = {
    "A": {"site": "context", "sites": "user", "user": True, "tool": False, "assistant": False, "prob": 0.001},
    "B": {"site": "context", "sites": "tool_result", "user": False, "tool": True, "assistant": False, "prob": 0.001},
    "C": {"site": "context", "sites": "user+tool_result", "user": True, "tool": True, "assistant": False, "prob": 0.001},
    "D": {"site": "tool", "sites": "tool_arg", "user": False, "tool": False, "assistant": False, "prob": 0.001},
    "E": {"site": "context", "sites": "assistant", "user": False, "tool": False, "assistant": True, "prob": 0.001},
    "baseline": {"site": "context", "sites": "none", "user": False, "tool": False, "assistant": False, "prob": 0.0},
}


def redact(text: str) -> str:
    """Never persist provider credentials copied from an agent transcript."""
    for secret in (os.environ.get("PIPE_OPENCODE_API_KEY", ""),):
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return _SECRET_RE.sub("[REDACTED]", text)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def iso_ms(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return None


def trajectory_parts(path: Path) -> tuple[str, str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (agent_text, final_answer, tool_calls, synthetic agent.jsonl rows)."""
    data = read_json(path)
    messages: list[str] = []
    calls: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for step in data.get("steps", []):
        if step.get("source") != "agent":
            continue
        ts = iso_ms(step.get("timestamp"))
        message = step.get("message") or ""
        if message:
            messages.append(str(message))
            events.append({"type": "text", "timestamp": ts, "part": {"text": str(message)}})
        for call in step.get("tool_calls") or []:
            fn = call.get("function_name") or ""
            args = call.get("arguments") or {}
            calls.append({"tool": fn, "args": args, "status": "completed", "ts": ts})
            events.append({
                "type": "tool",
                "timestamp": ts,
                "part": {"type": "tool", "tool": fn, "state": {"input": args, "status": "completed"}},
            })
    return "\n".join(messages), (messages[-1] if messages else ""), calls, events


def run_name(stamp: str, task: str, scheme: str, cfg: dict[str, Any]) -> str:
    mode = "burst8"
    return f"{stamp}_{task}_{scheme}_{cfg['sites']}_p{cfg['prob']:g}_{mode}_s1"


def latest_completed_job(root: Path) -> Path | None:
    jobs = [
        p for p in root.iterdir()
        if p.is_dir() and (p / "result.json").is_file()
    ] if root.is_dir() else []
    return max(jobs, key=lambda p: p.stat().st_mtime) if jobs else None


def ingest_trial(
    trial: Path,
    job: Path,
    parent: Path,
    scheme: str,
    task_root: Path,
    model: str,
    harness: str,
) -> Path | None:
    cfg = SCHEMES[scheme]
    tr = read_json(trial / "result.json")
    if not tr:
        return None
    task = str(tr.get("task_name") or trial.name).split("/")[-1]
    stamp = job.name.replace("-", "").replace("__", "-")
    stamp = stamp.replace("_", "-")
    # 2026-10-27__01-09-38 -> 20261027-010938
    try:
        stamp = datetime.strptime(job.name, "%Y-%m-%d__%H-%M-%S").strftime("%Y%m%d-%H%M%S")
    except ValueError:
        pass
    rid = run_name(stamp, task, scheme, cfg)
    dest = parent / rid
    if dest.exists():
        print(f"skip existing {dest}")
        return dest
    dest.mkdir(parents=True)

    agent_text, final_answer, calls, events = trajectory_parts(trial / "agent" / "trajectory.json")
    agent_text = redact(agent_text)
    final_answer = redact(final_answer)
    reward = ((tr.get("verifier_result") or {}).get("rewards") or {}).get("reward")
    reward = float(reward or 0.0)
    exc = (tr.get("exception_info") or {}).get("exception_type")
    fault = {"mode": "burst", "prob": cfg["prob"], "width": 8, "seed": 1}
    result = {
        "task": task,
        "model": model,
        "reward": reward,
        "partial": None,
        "detail": f"harbor {'error' if exc else 'run'}",
        "final_answer": final_answer,
        "gold": None,
        "tokens": tr.get("agent_result") or {},
        "returncode": 0 if not exc else 1,
        "attempts": 1,
        "injections": 0,
        "flips": 0,
        "metadata": {
            "kind": "terminal-bench",
            "job": job.name,
            "trial": trial.name,
            "exception": exc,
        },
        "fault": fault,
        "site": cfg["site"],
        "inject_user": cfg["user"],
        "inject_tool": cfg["tool"],
        "inject_assistant": cfg["assistant"],
        "scheme": scheme,
        "sites": cfg["sites"],
        "run_id": rid,
        "harness": harness,
    }

    raw_injections = read_jsonl(trial / "agent" / "fault-inject.log.jsonl")
    enriched: list[dict[str, Any]] = []
    behaviours: list[dict[str, Any]] = []
    for i, rec in enumerate(raw_injections, 1):
        rec = dict(rec)
        rec["run_id"] = rid
        rec["event_id"] = f"{rid}-{task}-{i}"
        rec["task_id"] = task
        enriched.append(rec)
        behaviours.append(label_injection(rec, result, agent_text, calls))

    result["injections"] = len(enriched)
    result["flips"] = sum(int(r.get("n_flips") or 0) for r in enriched)
    (dest / "result.json").write_text(redact(json.dumps(result, indent=2)) + "\n")
    (dest / "task.json").write_text(json.dumps({
        "name": task,
        "answer": None,
        "metadata": {"kind": "terminal-bench", "source": str(trial)},
        "instruction": (task_root / task / "instruction.md").read_text() if (task_root / task / "instruction.md").is_file() else "",
        "model": model,
        "fault": fault,
        "site": cfg["site"],
    }, indent=2) + "\n")
    with (dest / "injections.jsonl").open("w") as fh:
        for rec in enriched:
            fh.write(json.dumps(rec) + "\n")
    with (dest / "behavior.jsonl").open("w") as fh:
        for rec in behaviours:
            fh.write(json.dumps(rec) + "\n")
    with (dest / "agent.jsonl").open("w") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")

    outcomes = Counter(r["outcome"] for r in behaviours)
    actions = Counter(r["action"] for r in behaviours)
    summary = {
        "task": task,
        "reward": reward,
        "final_answer": final_answer,
        "gold": None,
        "n_injections": len(behaviours),
        "n_flips": result["flips"],
        "detected": sum(bool(r["detected"]) for r in behaviours),
        "silent_error": outcomes.get("silent_error", 0),
        "recovered": outcomes.get("recovered", 0) + outcomes.get("recovered_silently", 0),
        "detected_but_failed": outcomes.get("detected_but_failed", 0),
        "failed_unattributed": outcomes.get("failed_unattributed", 0),
        "outcomes": dict(outcomes),
        "actions": dict(actions),
    }
    (dest / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    src = trial / "agent" / "opencode.txt"
    if src.is_file():
        (dest / "opencode.txt").write_text(redact(src.read_text(errors="replace")))
    src = trial / "verifier" / "test-stdout.txt"
    if src.is_file():
        (dest / "verify.log").write_text(redact(src.read_text(errors="replace")))
    # Do not persist Harbor's raw config here: it contains the provider API key.
    generate_report(dest)
    print(f"imported {dest.relative_to(ROOT)}")
    return dest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--harbor-root", type=Path, default=ROOT / "harbor_results" / "tb10-direct")
    ap.add_argument("--runs-root", type=Path, default=ROOT / "runs")
    ap.add_argument("--model", default="deepseek-v4.1-flash")
    ap.add_argument("--harness", default="opencode")
    ap.add_argument("--bench", default="terminal-bench")
    ap.add_argument("--task-root", type=Path, default=ROOT.parent / "data" / "terminal-bench-2-1" / "tasks")
    args = ap.parse_args()

    parent = args.runs_root / f"{args.bench}-{args.model}-{args.harness}"
    parent.mkdir(parents=True, exist_ok=True)
    total = 0
    for scheme, cfg in SCHEMES.items():
        job = latest_completed_job(args.harbor_root / scheme)
        if job is None:
            print(f"no completed job for {scheme}")
            continue
        print(f"import {scheme}: {job.name}")
        for trial in sorted(job.iterdir()):
            if trial.is_dir() and (trial / "result.json").is_file():
                if ingest_trial(trial, job, parent, scheme, args.task_root, args.model, args.harness):
                    total += 1
    print(f"done: {total} runs -> {parent}")


if __name__ == "__main__":
    main()
