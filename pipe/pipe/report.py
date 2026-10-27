"""Static HTML report for one run directory (no external assets)."""

from __future__ import annotations

import difflib
import html
import json
from pathlib import Path
from typing import Any

from .analysis import analyze_run, read_jsonl


def _diff_html(before: str, after: str) -> str:
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile="before",
        tofile="after",
        n=2,
    )
    out: list[str] = []
    for line in diff:
        cls = "add" if line.startswith("+") else "del" if line.startswith("-") else "ctx"
        out.append(f'<span class="{cls}">{html.escape(line)}</span>')
    return "".join(out) or "(identical)"


def _pre(text: str, limit: int = 20000) -> str:
    if len(text) > limit:
        text = text[:limit] + f"\n... ({len(text) - limit} chars truncated)"
    return f"<pre>{html.escape(text)}</pre>"


def generate_report(run_dir: str | Path) -> Path:
    root = Path(run_dir)
    if (root / "behavior.jsonl").is_file() and (root / "summary.json").is_file():
        summary = json.loads((root / "summary.json").read_text())
    else:
        summary = analyze_run(root)

    result: dict[str, Any] = {}
    if (root / "result.json").is_file():
        result = json.loads((root / "result.json").read_text())
    task: dict[str, Any] = {}
    if (root / "task.json").is_file():
        task = json.loads((root / "task.json").read_text())
    behaviours = read_jsonl(root / "behavior.jsonl")
    fallback: dict[str, Any] = {}
    if (root / "fallback.json").is_file():
        fallback = json.loads((root / "fallback.json").read_text())

    rows = []
    for i, b in enumerate(behaviours, 1):
        where = html.escape(str(b.get("where") or b.get("site")))
        tool = html.escape(str(b.get("tool") or "user prompt"))
        meta = (
            f"event_id={html.escape(str(b.get('event_id')))} · "
            f"flips={b.get('n_flips')} / bytes={b.get('n_bytes')} · "
            f"detected=<b>{b.get('detected')}</b> · "
            f"action={html.escape(str(b.get('action')))} · "
            f"outcome=<b>{html.escape(str(b.get('outcome')))}</b>"
        )
        rows.append(
            f'<section class="event">'
            f'<h3>#{i} {where} · {tool}</h3>'
            f'<p class="meta">{meta}</p>'
            f'<h4>before</h4>{_pre(str(b.get("before") or ""))}'
            f'<h4>after</h4>{_pre(str(b.get("after") or ""))}'
            f'<h4>diff</h4><pre class="diff">'
            f'{_diff_html(str(b.get("before") or ""), str(b.get("after") or ""))}</pre>'
            f'</section>'
        )

    page = f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>fault-inject report · {html.escape(str(result.get('task')))}</title>
<style>
body {{ font: 14px/1.45 system-ui, sans-serif; max-width: 1100px; margin: 2rem auto; padding: 0 1rem; color:#1f2328; }}
h1,h2,h3,h4 {{ margin-bottom: .4rem; }}
table {{ border-collapse: collapse; margin: .8rem 0; }}
td,th {{ border: 1px solid #d0d7de; padding: .3rem .55rem; text-align: left; }}
pre {{ background:#f6f8fa; border:1px solid #d0d7de; padding:.7rem; overflow:auto; white-space:pre-wrap; word-break:break-word; }}
.diff .add {{ background:#dafbe1; }} .diff .del {{ background:#ffebe9; }} .diff .ctx {{ color:#57606a; }}
.event {{ border-top:1px solid #d0d7de; margin-top:1.4rem; padding-top:.8rem; }}
.meta {{ color:#57606a; }}
.good {{ color:#1a7f37; }} .bad {{ color:#cf222e; }}
</style></head><body>
<h1>Fault-injection report</h1>
<p><b>task</b> {html.escape(str(result.get('task')))} ·
<b>model</b> {html.escape(str(task.get('model') or ''))} ·
<b>fallback</b> {html.escape(str(fallback.get('primary_model') or 'none'))} →
{html.escape(str(fallback.get('used_model') or 'none'))} ·
<b>site</b> {html.escape(str(result.get('site') or task.get('site') or 'none'))} ·
<b>prompt</b> {html.escape(str(result.get('inject_user')))} ·
<b>tool</b> {html.escape(str(result.get('inject_tool')))} ·
<b>assistant</b> {html.escape(str(result.get('inject_assistant')))} ·
<b>fault</b> {html.escape(str((task.get('fault') or {})))}</p>
<table>
<tr><th>reward</th><th>gold</th><th>final answer</th><th>injections</th><th>detected</th><th>silent errors</th></tr>
<tr><td>{result.get('reward')}</td><td>{html.escape(str(result.get('gold') or ''))}</td>
<td>{html.escape(str(result.get('final_answer') or ''))[:500]}</td>
<td>{summary.get('n_injections')}</td><td>{summary.get('detected')}</td>
<td>{summary.get('silent_error')}</td></tr>
</table>
<p><b>outcomes</b> {html.escape(json.dumps(summary.get('outcomes', {})))} ·
<b>actions</b> {html.escape(json.dumps(summary.get('actions', {})))}</p>
<h2>Injections</h2>
{''.join(rows) if rows else '<p>No injections recorded.</p>'}
<h2>Task prompt</h2>{_pre(str(task.get('instruction') or ''), 8000)}
</body></html>"""
    out = root / "report.html"
    out.write_text(page)
    return out
