# pilot data

Small hand-picked subsets for fault-injection direction finding.  `math.jsonl`
is the original 5-task smoke set; `math30.jsonl` is the larger 30-task GSM8K
pilot.  Treat these as pilots, not benchmark scores.

| file | source | selection |
|---|---|---|
| `math.jsonl` | GSM8K test split (`openai/grade-school-math`) | first 5 |
| `math30.jsonl` | GSM8K test split (`openai/grade-school-math`) | first 30 |
| `knowledge.jsonl` | MMLU test split (`hendrycks/test`, `data/test/*.csv`) | 2 physics, 2 high-school math, 1 global facts |
| `longcontext.jsonl` | LongBench v2 (`THUDM/LongBench-v2`) + synthetic | 1 baseline-correct short task + 2 synthetic needle controls |

The real LongBench task was selected because the pilot model answers it correctly
without faults, so a wrong answer after injection is attributable.  The other
LongBench candidates had unstable/failed baselines in the pilot.  The two
synthetic needle controls are generated locally (deterministic) so the
long-context direction test has a stable baseline.

Original datasets retain their own licenses; these files are only for local
pilot experiments.
