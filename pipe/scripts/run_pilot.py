#!/usr/bin/env python3
"""Run the tiny direction-finding matrix (baseline + hook injection).

Examples:
    python3 scripts/run_pilot.py --bench math --limit 2
    python3 scripts/run_pilot.py --bench knowledge --seeds 1,2
    python3 scripts/run_pilot.py --bench longcontext --limit 1

The hook-based chat backend launches opencode on the host.  It does not use an
HTTP proxy.
"""

from __future__ import annotations

import argparse
import copy
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipe.analysis import analyze_runs, compare_runs  # noqa: E402
from pipe.config import load_config, run  # noqa: E402

CONFIGS = {
    "math": "configs/math/math-mimo-v2.6-flash-opencode/A_user_p0.0005_single_s1.yaml",
    "math30": "configs/math30/math30-mimo-v2.6-flash-free-opencode/A_user_p0.002_single_s1.yaml",
    "knowledge": "configs/knowledge/knowledge-mimo-v2.6-flash-opencode/A_user_p0.0005_single_s1.yaml",
    "longcontext": "configs/longcontext/longcontext-mimo-v2.6-flash-opencode/A_user_p0.00001_single_s1.yaml",
    "code": "configs/code/code-mimo-v2.6-flash-opencode/A_user_p0.001_single_s1.yaml",
    "code-go": "configs/code/code-deepseek-v4.1-flash-go-opencode/A_user_p0.001_single_s1.yaml",
}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--bench", choices=sorted(CONFIGS), action="append")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--seeds", default="1")
    p.add_argument("--model", default=None, help="override model")
    p.add_argument("--prob", type=float, default=None, help="override fault probability")
    p.add_argument("--tasks", default=None, help="comma-separated task ids")
    p.add_argument("--baseline", action="store_true", default=True)
    p.add_argument("--no-baseline", dest="baseline", action="store_false")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    benches = args.bench or sorted(CONFIGS)
    seeds = [int(x) for x in args.seeds.split(",") if x.strip()]
    for bench in benches:
        base = load_config(ROOT / CONFIGS[bench])
        if args.model:
            base.model = args.model
        if args.prob is not None:
            base.fault = replace(base.fault, prob=args.prob)
        if args.limit is not None:
            base.limit = args.limit
        if args.tasks:
            base.tasks = [x for x in args.tasks.split(",") if x.strip()]
        base.dry_run = args.dry_run
        variants = []
        if args.baseline:
            variants.append(("off", 0.0, 1))
        for seed in seeds:
            variants.append((f"fault-s{seed}", base.fault.prob, seed))

        for label, prob, seed in variants:
            cfg = copy.deepcopy(base)
            cfg.fault = replace(cfg.fault, prob=prob, seed=seed)
            cfg.dry_run = args.dry_run
            print(
                f"\n=== {bench} {label} mode={cfg.fault.mode} "
                f"prob={cfg.fault.prob:g} seed={seed} ==="
            )
            results = run(cfg)
            for r in results:
                print(
                    f"  {r.task:32s} reward={r.reward:<4} inj={r.injected_calls:<2} "
                    f"answer={str(r.final_answer)[:80]!r}"
                )
            if not args.dry_run:
                summary = analyze_runs(cfg.runs_dir)
                print(f"  summary: {summary['totals']}")
                compare = compare_runs(cfg.runs_dir)
                print(f"  paired rows: {len(compare['rows'])}")


if __name__ == "__main__":
    main()
