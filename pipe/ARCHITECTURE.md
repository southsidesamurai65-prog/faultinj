# Architecture

## 1. Pipeline

```
RunConfig
  │  backend + benchmark + model + fault + injection flags
  ▼
make_benchmark() ──► Task[]
  │  each Task has instruction, workdir, evaluator, image, metadata
  ▼
Backend.run(task, model, InjectOptions)
  │
  ├─ ChatBackend   host: opencode run
  ├─ DockerBackend container: docker run + verify
  └─ HarborBackend container: harbor run + verify
  │
  ▼
opencode fault plugin
  ├─ chat.message                         user prompt
  ├─ tool.execute.before                  tool arguments
  ├─ tool.execute.after                   tool results
  └─ experimental.chat.messages.transform assistant history
  │
  ▼
runs/<run_id>/
  ├─ task.json / result.json
  ├─ injections.jsonl
  ├─ agent.jsonl / agent.err
  └─ behavior.jsonl / summary.json / report.html
  │
  ▼
analysis.py ──► compare.csv
report.py   ──► report.html
```

The core design is **backend-neutral**. A `Task` knows how to evaluate itself;
a `Backend` knows how to execute it; a `ModelSpec` knows how to address the
model. Fault injection is a plugin concern, not a benchmark or model concern.

## 2. Layers

### Domain layer

| file | role |
|---|---|
| `pipe/types.py` | `Task`, `EvalResult`, `RunResult` value objects |
| `pipe/faults.py` | pure bit-flip models (`single`, `burst`), mulberry32 PRNG |
| `pipe/injector.py` | `Site`, `InjectOptions`, `InjectionRecord`; reference middleware |
| `pipe/runio.py` | run-directory naming, OpenCode JSONL parsing, session id, injection stats |
| `pipe/models.py` | model registry and OpenCode connection metadata |

### Benchmark layer

| file | role |
|---|---|
| `pipe/benchmarks.py` | task loaders and evaluators: TerminalBench, NL2Repo, MathQA, MathSelfCheckQA, KnowledgeQA, LongContextQA, LongContextSelfCheckQA, CodeQA |
| `pipe/imagebuild.py` | optional Docker image prebuild with agent toolchain |

### Backend layer

| file | role |
|---|---|
| `pipe/chat.py` | host-side `opencode run`; supports multi-turn follow-up and model fallback orchestration |
| `pipe/docker.py` | generic container backend; mounts Go auth/binary, runs verifier, restores pristine tests |
| `pipe/harbor.py` | Harbor backend for Terminal-Bench-format tasks |
| `pipe/harbor_agent/` | in-container Harbor OpenCode agent (`fault_opencode.py`) |

### Orchestration layer

| file | role |
|---|---|
| `pipe/config.py` | `RunConfig`, YAML/JSON loading, backend dispatch, free→Go fallback |
| `pipe/env.py` | `.env` loading without overriding process env |
| `pipe/__main__.py` | CLI: `run`, `analyze`, `compare`, `report`, `build-image`, `gui` |
| `pipe/gui.py` | local HTTP console/API; launches `pipe run` as one subprocess |
| `pipe/gui_assets/` | dependency-free HTML/CSS/JS for the console |
| `pipe/__init__.py` | public package exports |

### Analysis layer

| file | role |
|---|---|
| `pipe/analysis.py` | rule-based labels (`detected`, `action`, `outcome`), batch summaries, baseline pairing |
| `pipe/report.py` | static HTML report with before/after diff and final answer |

### Injection asset

| file | role |
|---|---|
| `pipe/assets/opencode-fault-inject.ts` | OpenCode plugin implementing all injection channels and JSONL logging |

## 3. Injection channels

| label | flag(s) | hook | target |
|---|---|---|---|
| A | `inject_user` | `chat.message` | user prompt |
| B | `inject_tool` | `tool.execute.after` | tool result |
| C | `inject_user` + `inject_tool` | both above | prompt + tool result |
| D | `site: tool` | `tool.execute.before` | `bash.command`, `write.content`, `edit.newString` |
| E | `inject_assistant` | `experimental.chat.messages.transform` | prior assistant text and tool-call arguments |

E on math uses the `math-selfcheck` benchmark: the first answer is produced,
then the second turn asks for a review. The plugin injects the first assistant
answer before the review request.

## 4. Run directory

```
runs/<bench>-<model>-<harness>/<timestamp>_<task>_<scheme>_<sites>_p<prob>_<mode><width>_s<seed>/
├─ task.json
├─ result.json
├─ injections.jsonl
├─ behavior.jsonl
├─ summary.json
├─ report.html
├─ command.txt
├─ agent.jsonl
├─ agent.err
├─ verify.log
└─ work/
```

Parents follow `<bench>-<model>-<harness>`; child names contain
`<scheme>_<sites>_p<prob>_<mode><width>_s<seed>`. Superseded runs are not
retained; `fallback.json` marks a run that succeeded after fallback.

## 5. Supporting directories

| path | role |
|---|---|
| `configs/<bench>/<bench>-<model>-<harness>/` | experiment configs |
| `scripts/run_pilot.py` | small baseline + injection matrix runner |
| `scripts/selfcheck.py` | dependency-light smoke check used when pytest is absent |
| `scripts/collect_injections.py` | build one browsable HTML file from all `behavior.jsonl` records |
| `tests/` | pytest unit tests |
| `../data/pilot/` | GSM8K / MMLU / LongBench pilot data |
| `../data/code/` | HumanEval pilot data |
| `../data/nl2repo/` | NL2Repo task definitions |
| `../data/terminal-bench-2-1/` | Terminal-Bench task definitions |
| `runs/` | generated experiment artifacts (gitignored) |

## 6. Design decisions

- **Hook-based injection, no proxy.** Free-tier traffic is rejected through a
  proxy, so all injection happens in the OpenCode plugin. Containers use direct
  egress (or the Docker daemon's own registry mirror), not an HTTP proxy.
- **Prompt-only is a configuration choice, not an injector limitation.** The
  plugin supports prompt, tool-result, tool-argument, and assistant-history
  injection.
- **Backends stay separate.** Host chat and container execution have different
  isolation, scoring, and model-auth constraints.
- **Container tests are restored before scoring.** `protect_tests: true` prevents
  an agent from modifying image tests to inflate its reward.
- **Only OpenCode is supported.** Codex has no equivalent tool hook, so its
  shell shim was removed rather than kept as a second injection path.
