# coding pilot data

`humaneval3.jsonl` is a 3-task subset of HumanEval (`openai/human-eval`):
`HumanEval/2` (truncate_number), `/3` (below_zero), `/4`
(mean_absolute_deviation).

These run **host-side** through `ChatBackend`: the agent writes `solution.py`,
then the evaluator runs the dataset's `check(candidate)` code.  No container or
network is needed at run time.  HumanEval retains its original license.
