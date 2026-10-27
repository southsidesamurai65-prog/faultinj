"""pipe: a fault-injection pipeline for agent benchmarks.

Backends:

* ``chat``   -> host-side ``opencode run`` for math / knowledge / long-context / code
* ``harbor`` -> Harbor (Terminal-Bench format)
* ``docker`` -> generic ``docker run`` (any task with an image, e.g. NL2Repo)

Injection is hook-based (OpenCode plugin): ``chat.message`` for the user
prompt, ``tool.execute.before/after`` for tool arguments/results, and
``experimental.chat.messages.transform`` for assistant history.  No HTTP proxy
is required.
Fault models live in ``faults.py``; analysis/reporting in ``analysis.py`` and
``report.py``.  See README.md for architecture and usage.
"""

from .analysis import analyze_run, analyze_runs, compare_runs
from .benchmarks import (
    BENCHMARKS,
    KnowledgeQA,
    LongContextQA,
    LongContextSelfCheckQA,
    MathQA,
    MathSelfCheckQA,
    NL2Repo,
    TerminalBench,
    extract_final_answer,
    make_benchmark,
    run_commands,
)
from .chat import ChatBackend, ChatConfig
from .config import RunConfig, load_config
from .config import run as run_pipeline
from .docker import DockerBackend, DockerConfig
from .env import load_env, parse_env_text
from .faults import FaultConfig, Faulted, Flip, apply_fault, mulberry32
from .harbor import HarborBackend, HarborConfig
from .imagebuild import build_image, default_tag
from .injector import FaultInjector, InjectOptions, InjectionRecord, Site
from .models import CONNECTIONS, MODELS, Connection, ModelSpec, resolve_connection, resolve_model
from .runio import read_injection_stats
from .types import EvalResult, RunResult, Task

__all__ = [
    "BENCHMARKS",
    "CONNECTIONS",
    "ChatBackend",
    "ChatConfig",
    "Connection",
    "DockerBackend",
    "DockerConfig",
    "EvalResult",
    "FaultConfig",
    "FaultInjector",
    "Faulted",
    "Flip",
    "HarborBackend",
    "HarborConfig",
    "InjectOptions",
    "InjectionRecord",
    "KnowledgeQA",
    "LongContextQA",
    "LongContextSelfCheckQA",
    "MODELS",
    "MathQA",
    "MathSelfCheckQA",
    "ModelSpec",
    "NL2Repo",
    "RunConfig",
    "RunResult",
    "Site",
    "Task",
    "TerminalBench",
    "analyze_run",
    "analyze_runs",
    "compare_runs",
    "apply_fault",
    "build_image",
    "default_tag",
    "extract_final_answer",
    "load_config",
    "load_env",
    "make_benchmark",
    "mulberry32",
    "parse_env_text",
    "read_injection_stats",
    "resolve_connection",
    "resolve_model",
    "run_commands",
    "run_pipeline",
]
