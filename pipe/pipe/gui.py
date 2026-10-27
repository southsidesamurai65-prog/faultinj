"""Dependency-free local web console for running pipe experiments.

Run it with::

    python -m pipe gui

The server binds to localhost by default, lists the YAML configs under
``pipe/configs``, and launches ``python -m pipe run`` as a subprocess.  It is a
small local control panel, not a multi-user service: there is no authentication
and only one experiment runs at a time.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

PACKAGE_ROOT = Path(__file__).resolve().parent.parent  # pipe/
CONFIG_ROOT = PACKAGE_ROOT / "configs"
RUNS_ROOT = PACKAGE_ROOT / "runs"
ASSET_ROOT = Path(__file__).resolve().parent / "gui_assets"
MAX_LOG_LINES = 5000


def _config_path(name: str) -> Path:
    candidate = (PACKAGE_ROOT / name).resolve()
    if not candidate.is_file() or CONFIG_ROOT.resolve() not in candidate.parents:
        raise ValueError(f"unknown config: {name}")
    return candidate


def _config_names() -> list[str]:
    return sorted(
        p.relative_to(PACKAGE_ROOT).as_posix()
        for p in CONFIG_ROOT.rglob("*")
        if p.is_file() and p.suffix in {".yaml", ".yml", ".json"}
    )


def _config_summary(name: str) -> dict[str, Any]:
    from .config import load_config

    path = _config_path(name)
    try:
        cfg = load_config(path)
        return {
            "name": name,
            "config": {
                "backend": cfg.backend,
                "model": cfg.model,
                "benchmark": cfg.benchmark,
                "benchmark_path": cfg.benchmark_path,
                "site": cfg.site.value,
                "prob": cfg.fault.prob,
                "limit": cfg.limit,
                "tasks": cfg.tasks,
                "inject_user": cfg.inject_user,
                "inject_tool": cfg.inject_tool,
                "inject_assistant": cfg.inject_assistant,
                "runs_dir": cfg.runs_dir,
            },
        }
    except Exception as exc:  # show broken configs in the UI instead of hiding them
        return {"name": name, "error": str(exc)}


def build_command(config: str, options: dict[str, Any]) -> list[str]:
    """Build an argv list for ``pipe run`` from UI overrides."""
    _config_path(config)
    args = [sys.executable, "-u", "-m", "pipe", "run", "--config", config]

    limit = options.get("limit")
    if limit not in (None, ""):
        args += ["--limit", str(int(limit))]

    tasks = str(options.get("tasks") or "").strip()
    if tasks:
        args += ["--tasks", tasks]

    model = str(options.get("model") or "").strip()
    if model:
        args += ["--model", model]

    prob = options.get("prob")
    if prob not in (None, ""):
        args += ["--prob", str(float(prob))]

    site = str(options.get("site") or "inherit")
    if site in {"context", "tool"}:
        args += ["--site", site]

    for key, positive, negative in (
        ("inject_user", "--inject-user", "--no-inject-user"),
        ("inject_tool", "--inject-tool", "--no-inject-tool"),
        ("inject_assistant", "--inject-assistant", "--no-inject-assistant"),
    ):
        value = str(options.get(key) or "inherit")
        if value == "on":
            args.append(positive)
        elif value == "off":
            args.append(negative)

    if options.get("dry_run"):
        args.append("--dry-run")
    return args


@dataclass
class Job:
    id: int
    config: str
    command: list[str]
    process: subprocess.Popen[str] | None = None
    thread: threading.Thread | None = None
    started: float = field(default_factory=time.time)
    ended: float | None = None
    returncode: int | None = None
    error: str | None = None
    _log: list[str] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def running(self) -> bool:
        return self.returncode is None

    def append(self, line: str) -> None:
        with self._lock:
            self._log.append(line.rstrip("\n"))
            if len(self._log) > MAX_LOG_LINES:
                del self._log[: len(self._log) - MAX_LOG_LINES]

    def log_since(self, offset: int) -> tuple[int, list[str]]:
        with self._lock:
            offset = max(0, min(offset, len(self._log)))
            return len(self._log), self._log[offset:]

    def snapshot(self) -> dict[str, Any]:
        now = self.ended or time.time()
        with self._lock:
            log_count = len(self._log)
        return {
            "id": self.id,
            "running": self.running,
            "config": self.config,
            "command": self.command,
            "started_at": self.started,
            "ended_at": self.ended,
            "elapsed_sec": round(now - self.started, 1),
            "returncode": self.returncode,
            "error": self.error,
            "log_count": log_count,
        }


class AppState:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._job: Job | None = None
        self._next_id = 1

    @property
    def job(self) -> Job | None:
        with self._guard:
            return self._job

    def start(self, config: str, options: dict[str, Any]) -> dict[str, Any]:
        command = build_command(config, options)
        with self._guard:
            current = self._job
            if current and current.running:
                raise RuntimeError("an experiment is already running")
            job = Job(id=self._next_id, config=config, command=command)
            self._next_id += 1
            self._job = job

        env = dict(os.environ)
        try:
            job.process = subprocess.Popen(
                command,
                cwd=str(PACKAGE_ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            job.error = str(exc)
            job.returncode = -1
            job.ended = time.time()
            raise
        job.append(f"$ {' '.join(command)}")
        job.thread = threading.Thread(target=self._pump, args=(job,), daemon=True)
        job.thread.start()
        return job.snapshot()

    def _pump(self, job: Job) -> None:
        assert job.process is not None
        try:
            assert job.process.stdout is not None
            for line in job.process.stdout:
                job.append(line)
            rc = job.process.wait()
        except Exception as exc:  # keep the failure visible in the UI
            job.error = str(exc)
            rc = -1
        finally:
            job.returncode = rc
            job.ended = time.time()
            job.append(f"[pipe-gui] process exited with code {rc}")

    def stop(self) -> bool:
        with self._guard:
            job = self._job
        if not job or not job.running or not job.process:
            return False
        try:
            job.process.send_signal(signal.SIGINT)
        except (OSError, ValueError):
            job.process.terminate()
        try:
            job.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            job.process.terminate()
            try:
                job.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                job.process.kill()
        return True


def _static_file(name: str) -> Path:
    allowed = {
        "index.html": ASSET_ROOT / "index.html",
        "app.js": ASSET_ROOT / "app.js",
        "style.css": ASSET_ROOT / "style.css",
    }
    try:
        path = allowed[name]
    except KeyError as exc:
        raise FileNotFoundError(name) from exc
    if not path.is_file():
        raise FileNotFoundError(name)
    return path


def _recent_runs(limit: int = 40) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for result_path in RUNS_ROOT.rglob("result.json"):
        rel = result_path.relative_to(RUNS_ROOT)
        if any(part.startswith("_") for part in rel.parts):
            continue
        try:
            result = json.loads(result_path.read_text())
            stat = result_path.stat()
        except (OSError, json.JSONDecodeError):
            continue
        run_dir = result_path.parent.relative_to(RUNS_ROOT).as_posix()
        report = result_path.parent / "report.html"
        items.append(
            {
                "run_dir": run_dir,
                "suite": Path(run_dir).parent.as_posix(),
                "task": result.get("task"),
                "model": result.get("model"),
                "reward": result.get("reward"),
                "injections": result.get("injections", 0),
                "flips": result.get("flips", 0),
                "site": result.get("site"),
                "mtime": stat.st_mtime,
                "report_url": (
                    f"/report?run={quote(run_dir)}" if report.is_file() else None
                ),
            }
        )
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items[:limit]


def _report_path(run_dir: str) -> Path:
    candidate = (RUNS_ROOT / run_dir / "report.html").resolve()
    if not candidate.is_file() or RUNS_ROOT.resolve() not in candidate.parents:
        raise FileNotFoundError(run_dir)
    return candidate


class Handler(BaseHTTPRequestHandler):
    server_version = "pipe-gui/0.1"
    state: AppState

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep the terminal clean; the browser shows the useful output.
        return

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: Any) -> None:
        self._send(
            code,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _error(self, code: int, message: str) -> None:
        self._json(code, {"error": message})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path in {"/", "/index.html"}:
                body = _static_file("index.html").read_bytes()
                self._send(200, body, "text/html; charset=utf-8")
                return
            if path.startswith("/static/"):
                name = path.removeprefix("/static/")
                body = _static_file(name).read_bytes()
                ctype = {
                    ".js": "application/javascript; charset=utf-8",
                    ".css": "text/css; charset=utf-8",
                }.get(Path(name).suffix, "application/octet-stream")
                self._send(200, body, ctype)
                return
            if path == "/api/configs":
                self._json(
                    200,
                    {
                        "configs": [
                            _config_summary(name) for name in _config_names()
                        ]
                    },
                )
                return
            if path == "/api/status":
                job = self.state.job
                self._json(
                    200,
                    {"job": job.snapshot() if job else None},
                )
                return
            if path == "/api/log":
                job = self.state.job
                if not job:
                    self._json(200, {"offset": 0, "lines": [], "job": None})
                    return
                raw = parse_qs(parsed.query).get("offset", ["0"])[0]
                try:
                    offset = int(raw)
                except ValueError:
                    offset = 0
                new_offset, lines = job.log_since(offset)
                self._json(
                    200,
                    {
                        "offset": new_offset,
                        "lines": lines,
                        "job": job.snapshot(),
                    },
                )
                return
            if path == "/api/runs":
                raw = parse_qs(parsed.query).get("limit", ["40"])[0]
                try:
                    limit = max(1, min(200, int(raw)))
                except ValueError:
                    limit = 40
                self._json(200, {"runs": _recent_runs(limit)})
                return
            if path == "/report":
                run_dir = parse_qs(parsed.query).get("run", [""])[0]
                body = _report_path(run_dir).read_bytes()
                self._send(200, body, "text/html; charset=utf-8")
                return
            self._error(404, "not found")
        except FileNotFoundError:
            self._error(404, "not found")
        except Exception as exc:
            self._error(500, str(exc))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object")
            if parsed.path == "/api/start":
                config = str(data.get("config") or "")
                if not config:
                    raise ValueError("config is required")
                job = self.state.start(config, data)
                self._json(200, {"job": job})
                return
            if parsed.path == "/api/stop":
                self._json(200, {"stopped": self.state.stop()})
                return
            self._error(404, "not found")
        except ValueError as exc:
            self._error(400, str(exc))
        except RuntimeError as exc:
            self._error(409, str(exc))
        except Exception as exc:
            self._error(500, str(exc))


def _run_server(host: str, port: int, open_browser: bool) -> None:
    state = AppState()
    handler = type("PipeGuiHandler", (Handler,), {"state": state})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    url = f"http://{host}:{port}/"
    print(f"pipe GUI: {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.stop()
        server.server_close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="pipe gui", description="local web console for running experiments"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    args = parser.parse_args(argv)
    _run_server(args.host, args.port, not args.no_open)


if __name__ == "__main__":
    main()
