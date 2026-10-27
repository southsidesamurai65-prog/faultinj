#!/usr/bin/env python3
"""Collect every injection event from ``runs/`` into one browsable HTML file.

Each event contains the model-visible text before/after injection plus the
agent's recorded reaction (detected/action/outcome/final answer).  The output is
self-contained and can be opened directly from disk.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent  # pipe/
DEFAULT_RUNS = ROOT / "runs"
DEFAULT_OUT = ROOT / "all-injections.html"


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def run_suite(rel: Path) -> str:
    parts = rel.parts
    return "/".join(parts[:-1]) if len(parts) > 1 else "runs"


def esc(value: Any) -> str:
    text = "" if value is None else str(value)
    # Keep the HTML text-safe: show non-printable control bytes as \xNN.
    text = "".join(
        ch if ch in "\n\t\r" or ord(ch) >= 32 and ord(ch) != 127 else f"\\x{ord(ch):02x}"
        for ch in text
    )
    return html.escape(text, quote=False)


def attr(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def slug(value: Any) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "unknown")).strip("-") or "unknown"


def collect(runs_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []

    for result_path in sorted(runs_root.rglob("result.json")):
        run_dir = result_path.parent
        rel = run_dir.relative_to(runs_root)
        if any(part.startswith("_") for part in rel.parts):
            continue
        result = read_json(result_path)
        task = read_json(run_dir / "task.json")
        summary = read_json(run_dir / "summary.json")
        run_id = run_dir.name
        suite = run_suite(rel)
        report = run_dir / "report.html"
        runs.append(
            {
                "suite": suite,
                "run_id": run_id,
                "run_dir": rel.as_posix(),
                "task": result.get("task") or task.get("name"),
                "model": result.get("model") or task.get("model"),
                "reward": result.get("reward"),
                "injections": result.get("injections", summary.get("n_injections", 0)),
                "flips": result.get("flips", summary.get("n_flips", 0)),
                "site": result.get("site") or task.get("site"),
                "scheme": result.get("scheme"),
                "prob": (result.get("fault") or {}).get("prob"),
                "detail": result.get("detail"),
                "exception": (result.get("metadata") or {}).get("exception"),
                "report_rel": f"runs/{rel.as_posix()}/report.html" if report.is_file() else None,
            }
        )

        behaviours = read_jsonl(run_dir / "behavior.jsonl")
        for index, record in enumerate(behaviours, 1):
            events.append(
                {
                    "suite": suite,
                    "run_id": run_id,
                    "run_dir": rel.as_posix(),
                    "event_index": index,
                    "task": record.get("task_id") or result.get("task"),
                    "model": result.get("model") or task.get("model"),
                    "scheme": result.get("scheme"),
                    "event_id": record.get("event_id"),
                    "site": record.get("site"),
                    "where": record.get("where"),
                    "tool": record.get("tool"),
                    "field": record.get("field"),
                    "mode": record.get("mode"),
                    "prob": record.get("prob"),
                    "seed": record.get("seed"),
                    "n_flips": record.get("n_flips"),
                    "n_bytes": record.get("n_bytes"),
                    "has_replacement": record.get("has_replacement"),
                    "detected": record.get("detected"),
                    "detection_signal": record.get("detection_signal"),
                    "action": record.get("action"),
                    "tool_action": record.get("tool_action"),
                    "tool_calls_after": record.get("tool_calls_after"),
                    "outcome": record.get("outcome"),
                    "reward": record.get("reward"),
                    "gold": record.get("gold"),
                    "final_answer": record.get("final_answer"),
                    "uses_corrupted_value": record.get("uses_corrupted_value"),
                    "inject_user": result.get("inject_user"),
                    "inject_tool": result.get("inject_tool"),
                    "inject_assistant": result.get("inject_assistant"),
                    "before": record.get("before") or "",
                    "after": record.get("after") or "",
                }
            )

    runs.sort(key=lambda r: (r["suite"], r["run_id"]))
    baseline_by_task: dict[tuple[str, str], dict[str, Any]] = {}
    for run in runs:
        try:
            is_zero = float(run.get("prob") or 0) == 0.0
        except (TypeError, ValueError):
            is_zero = False
        if run.get("scheme") == "baseline" or is_zero:
            baseline_by_task[(str(run.get("suite")), str(run.get("task")))] = run

    def attach_baseline(row: dict[str, Any]) -> None:
        if row.get("scheme") == "baseline":
            return
        base = baseline_by_task.get((str(row.get("suite")), str(row.get("task"))))
        row["baseline_reward"] = base.get("reward") if base else None
        row["baseline_run_id"] = base.get("run_id") if base else None
        row["baseline_report_rel"] = base.get("report_rel") if base else None
        row["baseline_exception"] = base.get("exception") if base else None

    for run in runs:
        attach_baseline(run)
    for event in events:
        attach_baseline(event)
    events.sort(key=lambda e: (e["suite"], e["run_id"], e["event_index"]))
    return events, runs


def reward_value(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def baseline_state(value: Any) -> str:
    number = reward_value(value)
    if number is None:
        return "na"
    return "pass" if number >= 1.0 else "fail"


def build_html(events: list[dict[str, Any]], runs: list[dict[str, Any]]) -> str:
    suite_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"runs": 0, "events": 0, "detected": 0, "silent": 0}
    )
    for run in runs:
        row = suite_counts[run["suite"]]
        row["runs"] += 1
    for event in events:
        row = suite_counts[event["suite"]]
        row["events"] += 1
        row["detected"] += int(bool(event["detected"]))
        row["silent"] += int(event["outcome"] == "silent_error")

    total_detected = sum(bool(e["detected"]) for e in events)
    total_silent = sum(e["outcome"] == "silent_error" for e in events)
    baseline_runs = []
    for r in runs:
        try:
            if float(r.get("prob") or 0) == 0.0:
                baseline_runs.append(r)
        except (TypeError, ValueError):
            pass
    baseline_failures = [
        r for r in baseline_runs
        if reward_value(r.get("reward")) is None or reward_value(r.get("reward")) < 1.0
    ]

    suite_rows = "".join(
        "<tr>"
        f"<td><code>{esc(suite)}</code></td>"
        f"<td>{counts['runs']}</td>"
        f"<td>{counts['events']}</td>"
        f"<td>{counts['detected']}</td>"
        f"<td>{counts['silent']}</td>"
        "</tr>"
        for suite, counts in sorted(suite_counts.items())
    )

    run_rows = "".join(
        "<tr>"
        f"<td class=\"suite\">{esc(run['suite'])}</td>"
        f"<td>{esc(run.get('scheme'))}</td>"
        f"<td class=\"task\">{esc(run['task'])}</td>"
        f"<td class=\"model\">{esc(run['model'])}</td>"
        f"<td class=\"num\">{esc(run['reward'])}</td>"
        f"<td class=\"num\">{esc(run['injections'])}</td>"
        f"<td class=\"num\">{esc(run['flips'])}</td>"
        f"<td>{esc(run['site'])}</td>"
        f"<td>{('<a href="' + attr(run['report_rel']) + '">open</a>') if run['report_rel'] else '—'}</td>"
        "</tr>"
        for run in runs
    )
    baseline_rows = "".join(
        "<tr>"
        f"<td class=\"suite\">{esc(run['suite'])}</td>"
        f"<td>{esc(run.get('scheme'))}</td>"
        f"<td class=\"task\">{esc(run['task'])}</td>"
        f"<td class=\"num\">{esc(run['reward'])}</td>"
        f"<td>{esc(run.get('exception') or '')}</td>"
        f"<td>{('<a href=\"' + attr(run['report_rel']) + '\">open</a>') if run['report_rel'] else '—'}</td>"
        "</tr>"
        for run in baseline_failures
    )

    cards: list[str] = []
    for event in events:
        outcome = event["outcome"] or "unknown"
        detected = "yes" if event["detected"] else "no"
        base_state = baseline_state(event.get("baseline_reward"))
        base_label = {"pass": "baseline pass", "fail": "baseline fail", "na": "baseline n/a"}[base_state]
        badges = [
            f'<span class="pill">{esc(event["suite"])}</span>',
            f'<span class="pill task">{esc(event["task"])}</span>',
            f'<span class="pill">{esc(event["where"])}{("/" + esc(event["tool"])) if event["tool"] else ""}</span>',
            f'<span class="pill">p={esc(event["prob"])}</span>',
            f'<span class="pill">flips={esc(event["n_flips"])}</span>',
            f'<span class="pill outcome-{slug(outcome)}">{esc(outcome)}</span>',
            f'<span class="pill baseline-{base_state}">{esc(base_label)}</span>',
        ]
        report_link = (
            f'<a href="runs/{attr(event["run_dir"])}/report.html">run report</a>'
            if (ROOT / "runs" / event["run_dir"] / "report.html").is_file()
            else ""
        )
        baseline_link = (
            f'<a href="{attr(event["baseline_report_rel"])}">baseline report</a>'
            if event.get("baseline_report_rel")
            else ""
        )
        final_answer = event.get("final_answer") or ""
        final_pre = (
            f"<details class=\"agent-text\"><summary>final answer · {len(final_answer)} chars</summary>"
            f"<pre>{esc(final_answer)}</pre></details>"
            if final_answer
            else "<p class=\"muted\">no final answer recorded</p>"
        )
        cards.append(
            f'<article class="event" '
            f'data-suite="{attr(event["suite"])}" data-outcome="{attr(outcome)}" '
            f'data-detected="{attr(detected)}" data-baseline="{attr(base_state)}">'
            f'<div class="event-head">{"".join(badges)}</div>'
            f'<div class="meta">'
            f'<code>{esc(event["event_id"])}</code> · '
            f'model <b>{esc(event["model"])}</b> · '
            f'<span class="mono">{esc(event["run_id"])}</span>'
            f'{(" · " + report_link) if report_link else ""}'
            f'{(" · " + baseline_link) if baseline_link else ""}'
            f'</div>'
            f'<div class="grid">'
            f'<section><h3>Injection before</h3>'
            f'<details><summary>{len(event["before"])} chars</summary><pre>{esc(event["before"])}</pre></details>'
            f'</section>'
            f'<section><h3>Injection after</h3>'
            f'<details><summary>{len(event["after"])} chars</summary><pre>{esc(event["after"])}</pre></details>'
            f'</section>'
            f'<section class="reaction"><h3>Agent reaction</h3>'
            f'<div class="reaction-grid">'
            f'<div><span class="label">detected</span><b>{esc(detected)}</b></div>'
            f'<div><span class="label">signal</span><b>{esc(event["detection_signal"])}</b></div>'
            f'<div><span class="label">action</span><b>{esc(event["action"])}</b></div>'
            f'<div><span class="label">tool action</span><b>{esc(event["tool_action"])}</b></div>'
            f'<div><span class="label">uses corrupted</span><b>{esc(event["uses_corrupted_value"])}</b></div>'
            f'<div><span class="label">run reward</span><b>{esc(event["reward"])}</b></div>'
            f'<div><span class="label">baseline</span><b>{esc(base_label)}</b></div>'
            f'<div><span class="label">gold</span><b>{esc(event["gold"])}</b></div>'
            f'</div>{final_pre}'
            f'</section>'
            f'</div></article>'
        )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pipe · all injection events</title>
<style>
:root {{ color-scheme: light; --ink:#1f2328; --muted:#57606a; --line:#d0d7de; --bg:#f6f8fa; --card:#fff; --accent:#0969da; --good:#1a7f37; --bad:#cf222e; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 28px 20px 60px; background: var(--bg); color: var(--ink); font: 13px/1.5 system-ui, -apple-system, Segoe UI, sans-serif; }}
main {{ max-width: 1500px; margin: 0 auto; }}
h1 {{ margin: 0 0 4px; font-size: 24px; }}
h2 {{ margin: 28px 0 10px; font-size: 16px; }}
h3 {{ margin: 0 0 6px; font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); }}
p {{ margin: 0; }}
code, pre, .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
.sub {{ color: var(--muted); margin-bottom: 18px; }}
.stats {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 14px 0 18px; }}
.stat {{ background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 9px 12px; min-width: 130px; }}
.stat b {{ display: block; font-size: 20px; }}
.stat span {{ color: var(--muted); }}
.filters {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 12px 0 18px; padding: 10px; background: var(--card); border: 1px solid var(--line); border-radius: 8px; }}
input, select {{ border: 1px solid var(--line); border-radius: 6px; background: #fff; color: var(--ink); padding: 7px 9px; font: inherit; }}
input[type="search"] {{ min-width: 280px; flex: 1; }}
label.check {{ display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }}
table {{ width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--line); }}
th, td {{ border-bottom: 1px solid var(--line); padding: 7px 9px; text-align: left; vertical-align: top; }}
th {{ font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); background: #f6f8fa; }}
td.num {{ font-family: ui-monospace, monospace; }}
td.task, td.model, td.suite {{ max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
details.run-index {{ margin-top: 8px; background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; }}
details.run-index summary {{ cursor: pointer; font-weight: 600; }}
details.run-index .table-wrap {{ max-height: 420px; overflow: auto; margin-top: 10px; }}
.event {{ background: var(--card); border: 1px solid var(--line); border-radius: 10px; margin: 12px 0; padding: 12px; }}
.event-head {{ display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 7px; }}
.pill {{ display: inline-block; border: 1px solid var(--line); border-radius: 999px; padding: 2px 7px; font: 11px ui-monospace, monospace; background: #f6f8fa; color: var(--muted); }}
.pill.task {{ color: var(--ink); background: #eef6ff; border-color: #b6d7ff; }}
.pill.outcome-silent_error {{ color: var(--bad); border-color: #ffb3b8; background: #fff0f0; }}
.pill.outcome-recovered, .pill.outcome-recovered_silently {{ color: var(--good); border-color: #aceebb; background: #f0fff4; }}
.pill.baseline-pass {{ color: var(--good); border-color: #aceebb; background: #f0fff4; }}
.pill.baseline-fail {{ color: var(--bad); border-color: #ffb3b8; background: #fff0f0; }}
.pill.baseline-na {{ color: var(--muted); }}
.meta {{ color: var(--muted); margin-bottom: 10px; overflow-wrap: anywhere; }}
.meta a {{ color: var(--accent); }}
.grid {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) minmax(260px, .8fr); gap: 10px; }}
.grid > section {{ min-width: 0; }}
details {{ border: 1px solid var(--line); border-radius: 6px; padding: 7px 9px; background: #fbfcfd; }}
details summary {{ cursor: pointer; color: var(--muted); }}
pre {{ margin: 8px 0 0; padding: 9px; max-height: 460px; overflow: auto; background: #f6f8fa; border: 1px solid var(--line); border-radius: 5px; white-space: pre-wrap; word-break: break-word; }}
.reaction-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 5px 10px; margin-bottom: 8px; }}
.reaction-grid > div {{ display: flex; justify-content: space-between; gap: 8px; border-bottom: 1px dotted var(--line); padding-bottom: 3px; }}
.label {{ color: var(--muted); }}
.agent-text {{ margin-top: 8px; }}
.muted {{ color: var(--muted); }}
.event[hidden] {{ display: none; }}
#visibleCount {{ color: var(--muted); margin-left: auto; font-family: ui-monospace, monospace; }}
@media (max-width: 1000px) {{ .grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body><main>
<h1>pipe · all injection events</h1>
<p class="sub">Before/after model-visible text and the recorded agent reaction. Baseline runs with 0 injection events are listed separately and attached to cards by task.</p>
<div class="stats">
  <div class="stat"><b>{len(runs)}</b><span>runs</span></div>
  <div class="stat"><b>{len(events)}</b><span>injection events</span></div>
  <div class="stat"><b>{total_detected}</b><span>detected</span></div>
  <div class="stat"><b>{total_silent}</b><span>silent errors</span></div>
  <div class="stat"><b>{len(baseline_failures)}</b><span>baseline failures</span></div>
</div>

<details class="run-index"><summary>Outcome legend</summary>
<table>
<thead><tr><th>label</th><th>meaning</th></tr></thead>
<tbody>
<tr><td><code>no_injection</code></td><td>before and after are identical; the fault did not change the target</td></tr>
<tr><td><code>recovered</code></td><td>agent explicitly detected the corruption and the final answer is correct</td></tr>
<tr><td><code>recovered_silently</code></td><td>no explicit detection, but the final answer is correct</td></tr>
<tr><td><code>silent_error</code></td><td>no explicit detection, corrupted content is used, and the answer is wrong</td></tr>
<tr><td><code>detected_but_failed</code></td><td>agent explicitly detected the corruption but still failed</td></tr>
<tr><td><code>failed_unattributed</code></td><td>wrong answer without explicit detection or a clear corrupted-value trace</td></tr>
</tbody>
</table>
</details>

<h2>By suite</h2>
<div class="table-wrap"><table>
<thead><tr><th>suite</th><th>runs</th><th>events</th><th>detected</th><th>silent</th></tr></thead>
<tbody>{suite_rows}</tbody>
</table></div>

<details class="run-index"><summary>Baseline failures ({len(baseline_failures)} of {len(baseline_runs)} p0 runs)</summary>
<div class="table-wrap"><table>
<thead><tr><th>suite</th><th>scheme</th><th>task</th><th>reward</th><th>exception</th><th>report</th></tr></thead>
<tbody>{baseline_rows or '<tr><td colspan="5" class="muted">No baseline failures.</td></tr>'}</tbody>
</table></div>
</details>

<details class="run-index"><summary>All runs ({len(runs)})</summary>
<div class="table-wrap"><table>
<thead><tr><th>suite</th><th>scheme</th><th>task</th><th>model</th><th>reward</th><th>inj</th><th>flips</th><th>site</th><th>report</th></tr></thead>
<tbody>{run_rows}</tbody>
</table></div>
</details>

<h2>Events</h2>
<div class="filters">
  <input type="search" id="search" placeholder="Search before / after / final answer / task…">
  <select id="suiteFilter"><option value="">all suites</option>{''.join(f'<option value="{attr(s)}">{esc(s)}</option>' for s in sorted(suite_counts))}</select>
  <select id="outcomeFilter"><option value="">all outcomes</option>{''.join(f'<option value="{attr(o)}">{esc(o)}</option>' for o in sorted({e["outcome"] or "unknown" for e in events}))}</select>
  <select id="detectedFilter"><option value="">detected: any</option><option value="yes">detected: yes</option><option value="no">detected: no</option></select>
  <select id="baselineFilter"><option value="">baseline: any</option><option value="pass">baseline: pass</option><option value="fail">baseline: fail</option><option value="na">baseline: n/a</option></select>
  <button id="expandAll" type="button">expand all</button>
  <button id="collapseAll" type="button">collapse all</button>
  <span id="visibleCount">{len(events)}/{len(events)}</span>
</div>
<div id="events">{''.join(cards) if cards else '<p class="muted">No injection events found.</p>'}</div>
</main>
<script>
const cards = [...document.querySelectorAll('.event')]
const search = document.getElementById('search')
const suite = document.getElementById('suiteFilter')
const outcome = document.getElementById('outcomeFilter')
const detected = document.getElementById('detectedFilter')
const baseline = document.getElementById('baselineFilter')
const count = document.getElementById('visibleCount')
function apply() {{
  const q = search.value.trim().toLowerCase()
  let visible = 0
  for (const card of cards) {{
    const ok = (!q || card.textContent.toLowerCase().includes(q))
      && (!suite.value || card.dataset.suite === suite.value)
      && (!outcome.value || card.dataset.outcome === outcome.value)
      && (!detected.value || card.dataset.detected === detected.value)
      && (!baseline.value || card.dataset.baseline === baseline.value)
    card.hidden = !ok
    if (ok) visible++
  }}
  count.textContent = `${{visible}}/${{cards.length}}`
}}
for (const el of [search, suite, outcome, detected, baseline]) el.addEventListener('input', apply)
document.getElementById('expandAll').addEventListener('click', () => document.querySelectorAll('details').forEach(d => d.open = true))
document.getElementById('collapseAll').addEventListener('click', () => document.querySelectorAll('details').forEach(d => d.open = false))
apply()
</script>
</body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, default=DEFAULT_RUNS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    events, runs = collect(args.runs)
    page = build_html(events, runs)
    args.out.write_text(page)
    print(f"wrote {args.out} ({len(events)} events, {len(runs)} runs, {len(page):,} bytes)")


if __name__ == "__main__":
    main()
