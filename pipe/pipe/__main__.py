"""Command-line entry point.

    python -m pipe run --config configs/harbor-opencode.yaml   # run in containers
    python -m pipe build-image --base <image> --agent opencode # prebuild an image
"""

from __future__ import annotations

import argparse
import os

from .config import load_config, run as run_config
from .env import load_env
from .imagebuild import build_image, default_tag


def _run_real(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="pipe run", description="run in task containers")
    p.add_argument("--config", required=True, help="YAML/JSON run config")
    p.add_argument("--limit", type=int, default=None, help="override task limit")
    p.add_argument("--dry-run", action="store_true", help="print commands only")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    if args.limit is not None:
        cfg.limit = args.limit
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
        if r.error:
            print(f"    note: {r.error}")
    passed = sum(1 for r in results if r.reward == 1.0)
    print(f"success: {passed}/{len(results)}")


def _build_image(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="pipe build-image", description="prebuild a task image")
    p.add_argument("--base", required=True, help="base task image (docker_image)")
    p.add_argument("--agent", choices=["opencode", "codex"], default="opencode")
    p.add_argument("--tag", default=None)
    p.add_argument("--proxy", default=None, help="default: $PIPE_CONTAINER_PROXY")
    p.add_argument("--no-pytest", action="store_true")
    p.add_argument("--force", action="store_true")
    args = p.parse_args(argv)

    proxy = args.proxy or os.environ.get("PIPE_CONTAINER_PROXY")
    tag = args.tag or default_tag(args.base, args.agent)
    print(f"building {tag} from {args.base} (agent={args.agent}, proxy={proxy or 'none'})")
    result = build_image(
        args.base, agent=args.agent, tag=tag, proxy=proxy,
        with_pytest=not args.no_pytest, force=args.force,
    )
    print(f"image ready: {result}")


def main(argv: list[str] | None = None) -> None:
    load_env()
    import sys

    args = sys.argv[1:] if argv is None else list(argv)
    if args and args[0] == "build-image":
        _build_image(args[1:])
    else:
        if args and args[0] == "run":
            args = args[1:]
        _run_real(args)


if __name__ == "__main__":
    main()
