# faultinj

Fault-injection experiments for coding and math agents.

- `pipe/` — the runner, injection plugin, analysis, reports, configs and tests.
- `data/` — benchmark task definitions and small pilot datasets.
- `pipe/runs/` — generated experiment artifacts (gitignored).

Experiment directories use:

```text
runs/<bench>-<model>-<harness>/
  <timestamp>_<task>_<scheme>_<sites>_p<prob>_<mode><width>_s<seed>/
```

For example `math30-deepseek-v4.1-flash-go-opencode/`. See
`pipe/configs/README.md`.

Start here:

- [pipe/README.md](pipe/README.md) — usage and injection channels
- [pipe/ARCHITECTURE.md](pipe/ARCHITECTURE.md) — data flow and file-by-file map
- [pipe/configs/README.md](pipe/configs/README.md) — experiment configs
- [data/README.md](data/README.md) — datasets and download instructions
- [sum.txt](sum.txt) — pilot and Terminal-Bench findings

Quick check:

```bash
cd pipe
python3 scripts/selfcheck.py
```

Local web console:

```bash
cd pipe
python3 -m pipe gui
```

Aggregate all injection events:

```bash
cd pipe
python3 scripts/collect_injections.py
# -> pipe/all-injections.html
```
