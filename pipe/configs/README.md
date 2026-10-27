# Configs

All paths are relative to `pipe/` (the directory you run `python -m pipe`
from).

## Layout

```text
configs/<bench>/<bench>-<model>-<harness>/
  <scheme>_<sites>_p<prob>_<mode><width>_s<seed>.yaml
```

- `<bench>`: `math`, `math30`, `knowledge`, `longcontext`, `code`, `nl2repo`,
  `terminal-bench`
- `<harness>`: currently `opencode` for every experiment. Backend details
  (`chat` / `docker` / `harbor`) stay inside the YAML and are not part of the
  path.
- `<scheme>`: `A`, `B`, `C`, `D`, `E`, or `baseline`
- `<sites>`:

  | scheme | sites |
  |---|---|
  | A | `user` |
  | B | `tool_result` |
  | C | `user+tool_result` |
  | D | `tool_arg` |
  | E | `assistant` |
  | baseline | `none` |

- `<prob>`: default fault probability, without the leading `p`
- `<mode><width>`: `single` for independent bit flips, `burstN` for N-byte
  bursts (e.g. `burst8`)
- `<seed>`: RNG seed, e.g. `s1`

All runs for one `<bench>-<model>-<harness>` share the same `runs_dir`:

```text
runs/<bench>-<model>-<harness>/
  <timestamp>_<task>_<scheme>_<sites>_p<prob>_<mode><width>_s<seed>/
```

The scheme/site/probability/mode are in every run directory name; there is no
combined `sites` directory. To produce a baseline from a scheme config, keep
the config and override `--prob 0`; the generated run name records `p0`.

## Chat / host-side OpenCode

| config | purpose |
|---|---|
| `math/math-mimo-v2.6-flash-opencode/A_user_p0.0005_single_s1.yaml` | 5-task GSM8K, A |
| `math30/math30-mimo-v2.6-flash-free-opencode/A_user_p0.002_single_s1.yaml` | 30-task GSM8K, A, free + Go fallback |
| `math30/math30-deepseek-v4.1-flash-go-opencode/B_tool_result_p0.002_single_s1.yaml` | B: tool-result injection (no-op for math) |
| `math30/math30-deepseek-v4.1-flash-go-opencode/C_user+tool_result_p0.002_single_s1.yaml` | C: prompt + tool-result injection |
| `math30/math30-deepseek-v4.1-flash-go-opencode/D_tool_arg_p0.002_single_s1.yaml` | D: tool-argument injection (no-op for math) |
| `math30/math30-deepseek-v4.1-flash-go-opencode/E_assistant_p0.005_single_s1.yaml` | E: assistant-history injection |
| `knowledge/knowledge-mimo-v2.6-flash-opencode/A_user_p0.0005_single_s1.yaml` | MMLU, A |
| `longcontext/longcontext-mimo-v2.6-flash-opencode/A_user_p0.00001_single_s1.yaml` | LongBench-v2 + synthetic needle, A |
| `longcontext/longcontext-deepseek-v4.1-flash-go-opencode/A_user_p0.001_single_s1.yaml` | A: user prompt, p=.001 |
| `longcontext/longcontext-deepseek-v4.1-flash-go-opencode/B_tool_result_p0.001_single_s1.yaml` | B: tool result (no-op) |
| `longcontext/longcontext-deepseek-v4.1-flash-go-opencode/C_user+tool_result_p0.001_single_s1.yaml` | C: prompt + tool result |
| `longcontext/longcontext-deepseek-v4.1-flash-go-opencode/D_tool_arg_p0.001_single_s1.yaml` | D: tool args (no-op) |
| `longcontext/longcontext-deepseek-v4.1-flash-go-opencode/E_assistant_p0.001_single_s1.yaml` | E: assistant history |
| `code/code-mimo-v2.6-flash-opencode/A_user_p0.001_single_s1.yaml` | host-side HumanEval, A |
| `code/code-deepseek-v4.1-flash-go-opencode/A_user_p0.001_single_s1.yaml` | host-side HumanEval, A, DeepSeek Go |

## Containers

| config | purpose |
|---|---|
| `nl2repo/nl2repo-deepseek-v4.1-flash-opencode/baseline_none_p0_burst8_s1.yaml` | generic NL2Repo baseline example |
| `terminal-bench/terminal-bench-deepseek-v4.1-flash-opencode/baseline_none_p0_burst8_s1.yaml` | generic Terminal-Bench baseline example |
| `terminal-bench/terminal-bench-deepseek-v4.1-flash-opencode/A_user_p0.001_burst8_s1.yaml` | Terminal-Bench 10-task matrix, A |
| `terminal-bench/terminal-bench-deepseek-v4.1-flash-opencode/B_tool_result_p0.001_burst8_s1.yaml` | Terminal-Bench 10-task matrix, B |
| `terminal-bench/terminal-bench-deepseek-v4.1-flash-opencode/C_user+tool_result_p0.001_burst8_s1.yaml` | Terminal-Bench 10-task matrix, C |
| `terminal-bench/terminal-bench-deepseek-v4.1-flash-opencode/D_tool_arg_p0.001_burst8_s1.yaml` | Terminal-Bench 10-task matrix, D |
| `terminal-bench/terminal-bench-deepseek-v4.1-flash-opencode/E_assistant_p0.001_burst8_s1.yaml` | Terminal-Bench 10-task matrix, E |
| `nl2repo/nl2repo-deepseek-v4.1-flash-go-opencode/A_user_p0.001_burst8_s1.yaml` | A: prompt-only |
| `nl2repo/nl2repo-deepseek-v4.1-flash-go-opencode/B_tool_result_p0.001_burst8_s1.yaml` | B: tool result |
| `nl2repo/nl2repo-deepseek-v4.1-flash-go-opencode/C_user+tool_result_p0.001_burst8_s1.yaml` | C: prompt + tool result |
| `nl2repo/nl2repo-deepseek-v4.1-flash-go-opencode/D_tool_arg_p0.001_burst8_s1.yaml` | D: tool args |
| `nl2repo/nl2repo-deepseek-v4.1-flash-go-opencode/E_assistant_p0.001_burst8_s1.yaml` | E: assistant history |

## Ad-hoc overrides

Prefer CLI overrides over copying a config:

```bash
# one task
python -m pipe run --config configs/nl2repo/nl2repo-deepseek-v4.1-flash-go-opencode/A_user_p0.001_burst8_s1.yaml \
  --tasks jsonlines --limit 1

# baseline: override probability; scheme/site stay descriptive
python -m pipe run --config configs/math30/math30-deepseek-v4.1-flash-go-opencode/B_tool_result_p0.002_single_s1.yaml \
  --prob 0

# change fault strength / site without editing YAML
python -m pipe run --config configs/nl2repo/nl2repo-deepseek-v4.1-flash-go-opencode/A_user_p0.001_burst8_s1.yaml \
  --tasks jsonlines,pyperclip --prob 0.005

python -m pipe run --config configs/math30/math30-deepseek-v4.1-flash-go-opencode/E_assistant_p0.005_single_s1.yaml \
  --inject-user --no-inject-tool --inject-assistant
```

When a run uses overrides that change the effective scheme/site/probability,
the generated run directory name records the effective values; the config
filename records its defaults.
