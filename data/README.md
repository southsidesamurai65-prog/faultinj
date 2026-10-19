# data — benchmark datasets

Task definitions for the benchmarks live here, decoupled from the experiment
code (`exp/`) and the framework (`pipe/`). The framework points at this
directory via `benchmark_path` in its config.

## Layout

```
data/
├─ terminal-bench-2-1/
│  └─ tasks/<task-name>/          # task.toml · instruction.md · tests/ · environment/ · solution/
└─ nl2repo/
   └─ test_files/<lib>/           # start.md (spec) · test_commands.json · test_case_count.txt · test_files.json
```

| benchmark | tasks | scoring | environment image |
|---|---|---|---|
| `terminal-bench` | 89 | in-container tests (binary) | Docker Hub (`docker_image` field) |
| `nl2repo` | 104 | upstream pytest pass rate (continuous) | `ghcr.io` (per-task image) |

> The repos contain **task definitions only** (spec / commands / counts / test
> file lists), **not the container images**. Images are pulled on demand at run
> time (one-time). When containers have no direct internet, pulling and model
> calls go through a host proxy.

## Download

```bash
bash data/download.sh
```

Or manually:

```bash
cd data
# Terminal-Bench 2.1
curl -L -o tb21.tar.gz https://codeload.github.com/harbor-framework/terminal-bench-2-1/tar.gz/refs/heads/main
tar xzf tb21.tar.gz && mv terminal-bench-2-1-main terminal-bench-2-1 && rm tb21.tar.gz
# NL2Repo-Bench
curl -L -o nl2repo.tar.gz https://codeload.github.com/multimodal-art-projection/NL2RepoBench/tar.gz/refs/heads/main
tar xzf nl2repo.tar.gz && mv NL2RepoBench-main nl2repo && rm nl2repo.tar.gz
```

## Reference in a config

```yaml
benchmark: terminal-bench
benchmark_path: ../data/terminal-bench-2-1/tasks
```

```yaml
benchmark: nl2repo
benchmark_path: ../data/nl2repo/test_files
```
