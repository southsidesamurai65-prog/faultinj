"""Shell-level fault injection shim (harness-agnostic, no harbor dependency).

Used by the codex Harbor agent and the docker backend: read-like commands are
shadowed under ``/tmp/fault-bin`` and their stdout is corrupted by
``fault_shim.py``.
"""

from __future__ import annotations

import base64
import json
import shlex

FAULT_BIN = "/tmp/fault-bin"
READ_CMDS = (
    "cat ls grep find head tail sed awk wc file stat strings od xxd nl tree python3 python"
)
DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

SHIM_PY = r'''import json, os, sys

def mulberry32(seed):
    a = seed & 0xFFFFFFFF
    def nxt():
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = ((a ^ (a >> 15)) * (1 | a)) & 0xFFFFFFFF
        t = ((t + (((t ^ (t >> 7)) * (61 | t)) & 0xFFFFFFFF)) & 0xFFFFFFFF) ^ t
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296
    return nxt

def main():
    raw = sys.stdin.buffer.read()
    try:
        cfg = json.load(open(os.environ.get("FAULT_CONFIG", "/tmp/fault-bin/fault.json")))
    except Exception:
        sys.stdout.buffer.write(raw); return
    if not cfg.get("enabled"):
        sys.stdout.buffer.write(raw); return
    prob = float(cfg.get("prob", 0.0)); mode = cfg.get("mode", "single")
    width = int(cfg.get("width", 8)); rnd = mulberry32(int(cfg.get("seed", 1)) + os.getpid())
    buf = bytearray(raw); flips = 0
    if mode == "burst":
        i = 0; n = len(buf)
        while i < n:
            if rnd() < prob:
                end = min(i + width, n)
                for j in range(i, end):
                    buf[j] ^= 0xFF; flips += 8
                i = end
            else:
                i += 1
    else:
        for i in range(len(buf)):
            for b in range(8):
                if rnd() < prob:
                    buf[i] ^= 1 << b; flips += 1
    sys.stdout.buffer.write(bytes(buf))
    try:
        with open("/tmp/fault-bin/fault-inject.log.jsonl", "a") as fh:
            fh.write(json.dumps({"n_flips": flips, "n_bytes": len(raw)}) + "\n")
    except Exception:
        pass

main()
'''


def shell_policy(path: str = DEFAULT_PATH) -> dict:
    """Codex ``shell_environment_policy`` that prepends the shim dir to PATH."""
    return {
        "inherit": "all",
        "set": {"PATH": f"{FAULT_BIN}:{path}", "FAULT_CONFIG": f"{FAULT_BIN}/fault.json"},
    }


def install_shim_command(fault_cfg: dict) -> str:
    """Shell command that writes the shim + config + per-command wrappers.

    The shim dir is put on PATH through several channels so the agent's shell
    picks it up regardless of how codex spawns commands:
      * codex ``shell_environment_policy`` (see :func:`shell_policy`)
      * the process env PATH (set by the caller for the agent exec)
      * /etc/profile.d, ~/.bashrc, ~/.profile (login/interactive shells)
    """
    b64_shim = base64.b64encode(SHIM_PY.encode()).decode()
    b64_cfg = base64.b64encode(json.dumps(fault_cfg).encode()).decode()
    cmds = " ".join(shlex.quote(c) for c in READ_CMDS.split())
    path_line = f"export PATH={FAULT_BIN}:$PATH"
    return (
        "set -e; "
        f"mkdir -p {FAULT_BIN}; "
        f"echo {shlex.quote(b64_shim)} | base64 -d > {FAULT_BIN}/fault_shim.py; "
        f"echo {shlex.quote(b64_cfg)} | base64 -d > {FAULT_BIN}/fault.json; "
        f"for c in {cmds}; do "
        f'  real="$(command -v "$c" 2>/dev/null || true)"; '
        f'  [ -n "$real" ] || continue; '
        f'  case "$real" in {FAULT_BIN}/*) continue;; esac; '
        f'  printf "#!/bin/bash\\nset -o pipefail\\n%s \\"\\$@\\" | python3 {FAULT_BIN}/fault_shim.py\\n" '
        f'"$real" > {FAULT_BIN}/"$c"; chmod +x {FAULT_BIN}/"$c"; '
        "done; "
        f"echo {shlex.quote(path_line)} > /etc/profile.d/00-fault-path.sh 2>/dev/null || true; "
        f"grep -q fault-bin ~/.bashrc 2>/dev/null || echo {shlex.quote(path_line)} >> ~/.bashrc; "
        f"grep -q fault-bin ~/.profile 2>/dev/null || echo {shlex.quote(path_line)} >> ~/.profile; "
        f"echo '[fault-shim] installed'; ls {FAULT_BIN}"
    )
