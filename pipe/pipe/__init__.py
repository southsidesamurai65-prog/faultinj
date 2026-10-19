"""pipe: a configurable, container-first fault-injection pipeline.

Backends (the agent always runs inside the task container):

* ``harbor`` -> Harbor (Terminal-Bench format), agent = FaultOpenCode / FaultCodex
* ``docker`` -> generic ``docker run`` (any task with an image, e.g. NL2Repo)

Fault models live in ``faults.py``; injection config in ``injector.py``.
See README.md for architecture and usage.
"""

from .benchmarks import BENCHMARKS, NL2Repo, TerminalBench, make_benchmark, run_commands
from .config import RunConfig, load_config
from .config import run as run_pipeline
from .docker import DockerBackend, DockerConfig
from .env import load_env, parse_env_text
from .faults import FaultConfig, Faulted, Flip, apply_fault, mulberry32
from .harbor import HarborBackend, HarborConfig
from .imagebuild import build_image, default_tag
from .injector import (
    FaultInjector,
    InjectOptions,
    InjectionRecord,
    Site,
    read_injection_stats,
)
from .models import CONNECTIONS, MODELS, Connection, ModelSpec, resolve_connection, resolve_model
from .types import EvalResult, RunResult, Task

__all__ = [
    "BENCHMARKS",
    "CONNECTIONS",
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
    "MODELS",
    "ModelSpec",
    "NL2Repo",
    "RunConfig",
    "RunResult",
    "Site",
    "Task",
    "TerminalBench",
    "apply_fault",
    "build_image",
    "default_tag",
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
