# pipe — Agent fault-injection pipeline (runs inside containers)

A bit-flip fault-injection pipeline **decoupled from harness / model / benchmark**.
The agent always runs **inside the task container**; bits are flipped at specific
points on the "tool call" causal path to observe the effect on task outcome.

## Architecture

```
                     ┌──────────── config.yaml ────────────┐
                     │ backend · harness · model · benchmark│
                     └──────────────────┬──────────────────┘
                                        ▼
  Benchmark.tasks() ─► Task ─► Backend.run(task, model, InjectOptions)
                                        │
              ┌─────────────────────────┴─────────────────────────┐
              ▼ backend = harbor                                    ▼ backend = docker
        HarborBackend                                          DockerBackend
        (Terminal-Bench format)                                (any task with an image)
              │                                                       │
   agent = FaultOpenCode / FaultCodex                     install injection → run agent → run verifier
              │                                                       │
              └───────────────────────┬───────────────────────────────┘
                                      ▼
                            in-container injection (per harness)
                opencode: plugin tool.execute.before/after
                codex   : /tmp/fault-bin shell shim (PATH shadow commands)
```

**Two injection sites** (`site`)

| site | target |
|---|---|
| `context` | **all tool results** (everything the model observes) |
| `tool` | **tool arguments** (`bash.command` / `write.content` / `edit.newString`) |

**Fault models** (`fault`)

| mode | semantics |
|---|---|
| `single` | every bit flips independently with probability `prob` |
| `burst` | each byte triggers with probability `prob`; a run of `width` (default 8) consecutive bytes, **all 8 bits of every byte flipped** |

PRNG is mulberry32; `seed` makes runs reproducible.

**Two backends** (`backend`)

| backend | fits | notes |
|---|---|---|
| `harbor` | Terminal-Bench format tasks | official runner: container isolation, image pull, install, scoring; agent = `FaultOpenCode` / `FaultCodex` |
| `docker` | any task with an `image` (e.g. NL2Repo) | generic `docker run` + in-container verifier; supports `build_image` prebuild |

**harness** (`harness` = the agent inside the container)

- `opencode`: installs the plugin + `fault.json`; the aggregated endpoint is
  registered as provider `pipe` (model id `pipe/<wire>`).
- `codex`: no opencode-style hook, so it uses a **shell shim**
  (`/tmp/fault-bin` shadows `cat/ls/grep/...`, stdout passes through
  `fault_shim.py`; activated via `shell_environment_policy` / env PATH).
  Only `context` is supported.

## Components

| module | responsibility |
|---|---|
| `pipe/faults.py` | fault models (pure functions) |
| `pipe/injector.py` | `InjectOptions` + injection-log parsing (`FaultInjector` is a reference middleware) |
| `pipe/_shim.py` | shell-level injection shim (used by codex) |
| `pipe/models.py` | model and endpoint registry (keys/URLs come from env vars) |
| `pipe/benchmarks.py` | `TerminalBench` / `NL2Repo` loaders |
| `pipe/harbor.py` | `HarborBackend` (harbor backend) |
| `pipe/docker.py` | `DockerBackend` (docker backend) |
| `pipe/imagebuild.py` | prebuild images (bake the agent toolchain into the task image) |
| `pipe/harbor_agent/` | in-Harbor fault agents: `FaultOpenCode` (plugin) / `FaultCodex` (shim) |
| `pipe/config.py` `env.py` `types.py` `__main__.py` | config / `.env` loading / value types / CLI |

## Usage

### 1. Install and test

```bash
cd pipe
python3 -m pytest -q
```

### 2. Configure environment variables (`.env`)

`python -m pipe` auto-loads `pipe/.env` (without overriding existing env vars).
**Keys and proxy go here, never into yaml.**

```ini
# opencode (go port, aggregated endpoint)
PIPE_OPENCODE_BASE_URL=https://REPLACE_ME/opencode
PIPE_OPENCODE_API_KEY=sk-REPLACE_ME

# codex (aggregated endpoint, OpenAI-compatible)
PIPE_CODEX_BASE_URL=https://REPLACE_ME/api
PIPE_CODEX_API_KEY=sk-REPLACE_ME

# proxy reachable from inside containers (host-forwarded address;
# used by build-image and the docker backend)
PIPE_CONTAINER_PROXY=http://REPLACE_ME:PORT
```

### 3. Dry run (print commands and artifacts, do not execute)

```bash
python -m pipe run --config configs/harbor-opencode.yaml --dry-run --limit 1
python -m pipe run --config configs/docker-codex.yaml   --dry-run --limit 1
```

### 4. Real run

Set `dry_run: false` in the config, then:

```bash
python -m pipe run --config configs/harbor-opencode.yaml
python -m pipe run --config configs/docker-codex.yaml --limit 5
```

> The `harbor` backend needs `harbor` available (`uv tool install harbor`);
> task images are pulled at run time. When containers have no direct internet,
> image pulls and model calls go through a host proxy (see `scripts/fwd_proxy.py`).

### 5. Prebuild images (optional: saves bandwidth + faster)

Bake node/npm + agent (+ pytest) into the task image; agent setup drops from
~8min to ~2s.

```bash
python -m pipe build-image --base alexgshaw/regex-log:20251031 --agent opencode
python -m pipe build-image --base <task-image> --agent codex --force
```

Or set `build_image: true` in a `docker` backend config; the derived tag
`pipe-<base>-<agent>:latest` is built automatically.

## Config fields (YAML/JSON)

```yaml
backend: harbor            # harbor | docker
harness: opencode          # opencode | codex
model: deepseek-v4.1-flash # deepseek-v4.1-flash | gpt-6-luna
benchmark: terminal-bench  # terminal-bench | nl2repo
benchmark_path: ../data/terminal-bench-2-1/tasks
site: context              # context | tool
fault:
  mode: burst              # single | burst
  prob: 0.001
  width: 8
  seed: 1
tools: []
log_path: fault-inject.log.jsonl
limit: 1
dry_run: true

# backend=harbor
results_dir: harbor_results
prebuilt: true             # task image already ships the agent toolchain (skip install)

# backend=docker
image_template: "ghcr.io/multimodal-art-projection/nl2repobench/{name}:latest"
container_workdir: /app
build_image: false
agent_setup:               # install the agent in-container when the image lacks it
  - "apt-get update && apt-get install -y curl nodejs npm"
  - "npm i -g @openai/codex"
```

## Model × harness (orthogonal)

Connections are **per harness**: each harness has one aggregated endpoint
(one url + one key), shared by both models.

|  | opencode | codex |
|---|---|---|
| `deepseek-v4.1-flash` | `configs/*-opencode.yaml` | `configs/*-codex.yaml` |
| `gpt-6-luna` | same (change `model`) | same |

| harness | environment variables |
|---|---|
| `opencode` | `PIPE_OPENCODE_BASE_URL`, `PIPE_OPENCODE_API_KEY` |
| `codex` | `PIPE_CODEX_BASE_URL`, `PIPE_CODEX_API_KEY` |

## Layout

```
pipe/
├─ .env / .env.example   # environment variables (incl. PIPE_CONTAINER_PROXY)
├─ configs/              # harbor/docker × opencode/codex
├─ scripts/fwd_proxy.py  # container egress forwarder (0.0.0.0:19829 -> host proxy)
├─ pipe/
│  ├─ assets/            # opencode fault plugin template
│  ├─ harbor_agent/      # in-Harbor fault agents (opencode / codex)
│  ├─ _shim.py  faults.py  injector.py
│  ├─ models.py  benchmarks.py  harbor.py  docker.py  imagebuild.py
│  ├─ config.py  env.py  types.py  __main__.py
└─ tests/
```
