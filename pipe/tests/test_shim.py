import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipe._shim import SHIM_PY, install_shim_command, shell_policy  # noqa: E402


def _run_shim(tmp_path, cfg, data: bytes) -> bytes:
    shim = tmp_path / "fault_shim.py"
    shim.write_text(SHIM_PY)
    cfg_f = tmp_path / "fault.json"
    cfg_f.write_text(json.dumps(cfg))
    env = dict(os.environ, FAULT_CONFIG=str(cfg_f))
    proc = subprocess.run(
        [sys.executable, str(shim)], input=data, capture_output=True, env=env
    )
    return proc.stdout


def test_shim_disabled_passthrough(tmp_path):
    data = b"hello world this is a test"
    out = _run_shim(tmp_path, {"enabled": False}, data)
    assert out == data


def test_shim_burst_flips_all_bytes(tmp_path):
    data = b"abcdefghijklmnop"
    cfg = {"enabled": True, "mode": "burst", "prob": 1.0, "width": 4, "seed": 1}
    out = _run_shim(tmp_path, cfg, data)
    assert len(out) == len(data)
    assert out != data
    # prob=1.0 -> every byte XOR 0xFF
    assert out == bytes(b ^ 0xFF for b in data)


def test_shim_single_prob_zero_is_identity(tmp_path):
    data = b"the quick brown fox"
    out = _run_shim(tmp_path, {"enabled": True, "mode": "single", "prob": 0.0}, data)
    assert out == data


def test_install_command_and_policy():
    cmd = install_shim_command({"enabled": True})
    assert "fault_shim.py" in cmd and "fault.json" in cmd
    assert "/etc/profile.d/00-fault-path.sh" in cmd
    assert "/tmp/fault-bin" in cmd
    policy = shell_policy()
    assert policy["set"]["PATH"].startswith("/tmp/fault-bin:")
