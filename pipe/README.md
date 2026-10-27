# pipe — agent fault injection

`pipe` injects bit flips into the text an agent sees and measures whether the
agent notices, corrects, or silently accepts the corruption.

It supports two execution modes:

- **host chat**: `opencode run` on the host for GSM8K / MMLU / LongBench /
  HumanEval;
- **containers**: Docker or Harbor for NL2Repo / Terminal-Bench.

Injection is implemented with OpenCode plugin hooks. There is no HTTP proxy.

## Naming

Result suites use one directory per benchmark/model/harness:

```text
runs/<bench>-<model>-<harness>/
  <timestamp>_<task>_<scheme>_<sites>_p<prob>_<mode><width>_s<seed>/
```

Examples:

```text
runs/math30-deepseek-v4.1-flash-go-opencode/
  20261025-230659_gsm8k-0_A_user_p0_single_s1/
  20261025-231129_gsm8k-0_C_user+tool_result_p0.002_single_s1/
```

`scheme` is `A`/`B`/`C`/`D`/`E` or `baseline`; `sites` is the actual
location(s), e.g. `user`, `tool_result`, `user+tool_result`, `tool_arg`,
`assistant`, or `none`. `single` means independent per-bit flips; `burstN`
means N-byte bursts. Model and harness live in the parent directory, so they
are not repeated in every run name.

Configs mirror this layout under `configs/<bench>/<bench>-<model>-<harness>/`.
See [configs/README.md](configs/README.md).

## Injection channels

| label | config | model-visible location |
|---|---|---|
| A | `site: context`, `inject_user: true`, `inject_tool: false` | user prompt |
| B | `site: context`, `inject_user: false`, `inject_tool: true` | tool results |
| C | `site: context`, `inject_user: true`, `inject_tool: true` | prompt + tool results |
| D | `site: tool` | tool arguments (`bash`, `write`, `edit`) |
| E | `inject_assistant: true` | agent's own prior text/tool-call history |

`math` and `longcontext` have no tool calls, so B/D are no-ops there. E uses a
two-turn self-check (`math-selfcheck` / `longcontext-selfcheck`): answer once,
then review after the first answer is injected. The long-context variant asks
for a one-sentence explanation so the first answer is long enough for E.

## Architecture

```
configs/**.yaml
      │
      ▼
RunConfig ──► Benchmark.tasks() ──► Task
      │                              │
      │                              ▼
      └────────────────────► Backend.run(task, model, InjectOptions)
                                     │
                 ┌───────────────────┼───────────────────┐
                 ▼                   ▼                   ▼
             ChatBackend         DockerBackend       HarborBackend
             (host opencode)     (docker run)        (harbor run)
                 │                   │                   │
                 └───────────────────┴───────────────────┘
                                     ▼
                         opencode fault plugin
             chat.message / tool.execute.before|after
             experimental.chat.messages.transform
                                     │
                                     ▼
              runs/<bench>-<model>-<harness>/<run_id>/{result,injections,behavior,report}
                                     │
                                     ▼
                         analysis.py ──► report.py
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the detailed data flow and a
file-by-file map.

## Quick start

```bash
cd pipe
python3 scripts/selfcheck.py           # no pytest required
python3 -m pytest -q                   # if pytest is installed
```

## Web GUI

A dependency-free local console can list configs, launch runs with overrides,
stream logs, stop the active run, and open generated reports:

```bash
cd pipe
python3 -m pipe gui
# http://127.0.0.1:8765/
```

Use `--host` / `--port` to change the bind address, and `--no-open` to keep the
browser closed. The server is intended for local single-user use; it has no
authentication and allows one active run at a time.

## Aggregate injection file

Build one self-contained HTML file containing every recorded injection event
(before/after text plus the agent reaction, final answer and per-run report
link):

```bash
cd pipe
python3 scripts/collect_injections.py
# -> all-injections.html
```

Use `--out` to choose a different path. Paths containing `_` are treated as
debug/failed runs and skipped.

## Host chat runs

```bash
python3 scripts/run_pilot.py --bench math30 --seeds 1
python3 scripts/run_pilot.py --bench code-go --seeds 1
python3 scripts/run_pilot.py --bench math --seeds 1,2 --prob 0.005

python -m pipe run --config configs/math30/math30-mimo-v2.6-flash-free-opencode/A_user_p0.002_single_s1.yaml --limit 1
python -m pipe run --config configs/code/code-deepseek-v4.1-flash-go-opencode/A_user_p0.001_single_s1.yaml --limit 1
```

`run_pilot.py` supports `--bench`, `--limit`, `--seeds`, `--model`, `--prob`,
`--tasks`, `--baseline/--no-baseline`, and `--dry-run`.

The `math30-mimo-v2.6-flash-free-opencode` config prefers the free model and falls
back to OpenCode Go; the smaller MiMo pilot suites use Go directly. Failed
transport attempts are kept under `_failed/` when a fallback succeeds;
successful fallback runs carry `fallback.json`.

## Container runs

```bash
python -m pipe run --config configs/nl2repo/nl2repo-deepseek-v4.1-flash-go-opencode/A_user_p0.001_burst8_s1.yaml
python -m pipe run --config configs/nl2repo/nl2repo-deepseek-v4.1-flash-go-opencode/B_tool_result_p0.001_burst8_s1.yaml
python -m pipe run --config configs/nl2repo/nl2repo-deepseek-v4.1-flash-go-opencode/E_assistant_p0.001_burst8_s1.yaml

# one task only
python -m pipe run --config configs/nl2repo/nl2repo-deepseek-v4.1-flash-go-opencode/A_user_p0.001_burst8_s1.yaml \
  --tasks jsonlines --limit 1
```

The `nl2repo-*.yaml` configs mount the host `opencode` binary and auth file read-only
into the container. In WSL2, start from a fresh login shell if the current
shell predates the `docker` group change.

Container NL2Repo runs set `protect_tests: true`: the image's `tests/`
directory is restored before verification, so the agent cannot inflate its
score by editing tests.

## Outcomes

`analysis.py` labels every injection event:

| outcome | meaning |
|---|---|
| `no_injection` | before/after are identical; the fault did not change the target |
| `recovered` | agent explicitly detected the corruption and still produced a correct answer |
| `recovered_silently` | agent did not explicitly detect it, but the final answer is still correct |
| `silent_error` | agent did not detect it, accepted corrupted content, and the answer is wrong |
| `detected_but_failed` | agent detected the corruption but still failed the task |
| `failed_unattributed` | wrong answer without an explicit detection or clear use of corrupted content |

`detected` is true when the agent mentions an anomaly, says it is retrying, or
uses an alternate verification path. Superseded/failed runs are not kept in the current
tree; any path containing `_` is skipped by the collector.

## Artifacts

```text
runs/<bench>-<model>-<harness>/<timestamp>_<task>_<scheme>_<sites>_p<prob>_<mode><width>_s<seed>/
├─ task.json          task, instruction, model, fault config
├─ result.json        reward, final answer, tokens, injection counts
├─ injections.jsonl   before/after for every injected span
├─ behavior.jsonl     detected/action/outcome labels
├─ summary.json       per-run aggregate
├─ report.html        static before/after report
├─ agent.jsonl        raw opencode JSON events
├─ verify.log         container verifier output
└─ work/              agent workspace (host chat)
```

Batch commands:

```bash
python -m pipe analyze runs/math30-deepseek-v4.1-flash-go-opencode
python -m pipe compare runs/math30-deepseek-v4.1-flash-go-opencode
python -m pipe report runs/math30-deepseek-v4.1-flash-go-opencode/<run>
```

## Layout

```text
pipe/
├─ configs/             # <bench>/<bench>-<model>-<harness>/ configs; see configs/README.md
├─ pipe/                # Python package
│  ├─ assets/           # OpenCode plugin
│  ├─ gui_assets/       # local console HTML/CSS/JS
│  ├─ harbor_agent/     # Harbor OpenCode agent
│  ├─ analysis.py       # behavior labels + compare
│  ├─ benchmarks.py     # task loaders/evaluators
│  ├─ chat.py           # host-side OpenCode backend
│  ├─ config.py         # RunConfig + orchestration
│  ├─ docker.py         # Docker backend
│  ├─ faults.py         # bit-flip models
│  ├─ gui.py            # local web console/API
│  ├─ injector.py       # injector abstraction + logging types
│  ├─ models.py         # model registry
│  ├─ report.py         # HTML report
│  ├─ runio.py          # shared run-dir/JSONL parsing
│  └─ types.py          # Task / EvalResult / RunResult
├─ scripts/             # run_pilot, selfcheck
└─ tests/               # pytest tests

../data/                 # GSM8K / MMLU / LongBench / HumanEval / NL2Repo / TB
```

## Notes

- Keys and endpoints belong in `.env`, never in YAML.
- Every backend uses the same OpenCode plugin; there is no shell shim and no
  HTTP proxy.
- Docker Hub may need a registry mirror; GHCR images used by NL2Repo worked
  directly in this environment.
