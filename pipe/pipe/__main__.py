"""Command-line entry point.

    python -m pipe run --config configs/terminal-bench/terminal-bench-deepseek-v4.1-flash-opencode/baseline_none_p0_burst8_s1.yaml
    python -m pipe run --config configs/math/math-mimo-v2.6-flash-opencode/A_user_p0.0005_single_s1.yaml
    python -m pipe build-image --base <image>                    # prebuild an image
    python -m pipe analyze runs/<dir>                          # labels + summary
    python -m pipe report  runs/<dir>                          # static HTML
    python -m pipe gui                                         # local web console
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace

from .analysis import analyze_run, analyze_runs, compare_runs
from .config import load_config, run as run_config
from .env import load_env
from .injector import Site
from .imagebuild import build_image, default_tag
from .report import generate_report


def _run_real(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="pipe run", description="run a chat or container benchmark")
    p.add_argument("--config", required=True, help="YAML/JSON run config")
    p.add_argument("--limit", type=int, default=None, help="override task limit")
    p.add_argument("--tasks", default=None, help="comma-separated task ids")
    p.add_argument("--model", default=None, help="override model")
    p.add_argument("--prob", type=float, default=None, help="override fault probability")
    p.add_argument("--site", choices=["context", "tool"], default=None)
    p.add_argument(
        "--inject-user", action=argparse.BooleanOptionalAction, default=None,
        help="inject the user prompt (context site)",
    )
    p.add_argument(
        "--inject-tool", action=argparse.BooleanOptionalAction, default=None,
        help="inject tool results (context site)",
    )
    p.add_argument(
        "--inject-assistant", action=argparse.BooleanOptionalAction, default=None,
        help="inject the agent's own prior text/tool calls",
    )
    p.add_argument("--dry-run", action="store_true", help="print commands only")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    if args.limit is not None:
        cfg.limit = args.limit
    if args.tasks:
        cfg.tasks = [x for x in args.tasks.split(",") if x.strip()]
    if args.model:
        cfg.model = args.model
    if args.prob is not None:
        cfg.fault = replace(cfg.fault, prob=args.prob)
    if args.site:
        cfg.site = Site(args.site)
    if args.inject_user is not None:
        cfg.inject_user = args.inject_user
    if args.inject_tool is not None:
        cfg.inject_tool = args.inject_tool
    if args.inject_assistant is not None:
        cfg.inject_assistant = args.inject_assistant
    if args.dry_run:
        cfg.dry_run = True

    print(
        f"run: backend={cfg.backend} harness={cfg.harness} model={cfg.model} "
        f"benchmark={cfg.benchmark} site={cfg.site.value} mode={cfg.fault.mode} "
        f"prob={cfg.fault.prob} width={cfg.fault.width} seed={cfg.fault.seed} "
        f"dry_run={cfg.dry_run}"
    )
    results = run_config(cfg)
    print(f"{'task':32s} {'reward':>6s} {'partial':>10s} {'inj':>5s} {'flips':>7s}")
    for r in results:
        print(
            f"{r.task:32s} {r.reward:>6} {str(r.partial):>10s} "
            f"{r.injected_calls:>5d} {r.total_flips:>7d}"
        )
        if r.final_answer:
            print(f"    answer: {r.final_answer[:160]}")
        if r.run_dir:
            print(f"    run_dir: {r.run_dir}")
        if r.error:
            print(f"    note: {r.error}")
    passed = sum(1 for r in results if r.reward == 1.0)
    print(f"success: {passed}/{len(results)}")


def _build_image(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="pipe build-image", description="prebuild a task image")
    p.add_argument("--base", required=True, help="base task image (docker_image)")
    p.add_argument("--tag", default=None)
    p.add_argument("--no-pytest", action="store_true")
    p.add_argument("--force", action="store_true")
    args = p.parse_args(argv)

    tag = args.tag or default_tag(args.base, "opencode")
    print(f"building {tag} from {args.base}")
    result = build_image(
        args.base, agent="opencode", tag=tag,
        with_pytest=not args.no_pytest, force=args.force,
    )
    print(f"image ready: {result}")


def _analyze(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="pipe analyze", description="label a run")
    p.add_argument("path", help="run directory, or a parent runs/ directory")
    args = p.parse_args(argv)
    path = args.path
    if (os.path.isdir(path) and os.path.isfile(os.path.join(path, "result.json"))):
        print(json.dumps(analyze_run(path), indent=2))
    else:
        print(json.dumps(analyze_runs(path), indent=2))


def _compare(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="pipe compare", description="pair baseline and injected runs")
    p.add_argument("path", help="parent runs/ directory")
    args = p.parse_args(argv)
    print(json.dumps(compare_runs(args.path), indent=2))


def _report(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="pipe report", description="write report.html")
    p.add_argument("path", help="run directory")
    args = p.parse_args(argv)
    print(generate_report(args.path))


def main(argv: list[str] | None = None) -> None:
    load_env()
    import sys

    args = sys.argv[1:] if argv is None else list(argv)
    if args and args[0] == "gui":
        from .gui import main as gui_main

        gui_main(args[1:])
    elif args and args[0] == "build-image":
        _build_image(args[1:])
    elif args and args[0] == "analyze":
        _analyze(args[1:])
    elif args and args[0] == "report":
        _report(args[1:])
    elif args and args[0] == "compare":
        _compare(args[1:])
    else:
        if args and args[0] == "run":
            args = args[1:]
        _run_real(args)


if __name__ == "__main__":
    main()
