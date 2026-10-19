"""Prebuild a task image with the agent toolchain baked in.

Baking node/npm + agent (and pytest) into the image avoids re-downloading the
toolchain on every trial and cuts agent setup from minutes to seconds.

The base image is expected to be Debian/Ubuntu-like (apt). Alpine needs apk.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

AGENT_PACKAGES = {
    "opencode": "opencode-ai@latest",
    "codex": "@openai/codex",
}
AGENT_VERSION_CMD = {
    "opencode": "opencode --version",
    "codex": "codex --version",
}
PYTEST_PINS = "pytest==8.4.1 pytest-json-ctrf==0.3.5"


def default_tag(base_image: str, agent: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "-", base_image).strip("-")
    return f"pipe-{safe}-{agent}:latest"


def dockerfile_for(base_image: str, agent: str, with_pytest: bool = True) -> str:
    pkg = AGENT_PACKAGES[agent]
    version_cmd = AGENT_VERSION_CMD[agent]
    pytest_line = (
        f" && pip install --break-system-packages --no-cache-dir {PYTEST_PINS}"
        if with_pytest
        else ""
    )
    return (
        f"FROM {base_image}\n"
        "\n"
        "ARG http_proxy\nARG https_proxy\nARG HTTP_PROXY\nARG HTTPS_PROXY\n"
        "\n"
        "RUN apt-get update \\\n"
        " && apt-get install -y --no-install-recommends \\\n"
        "      curl bash coreutils nodejs npm ca-certificates python3-pip \\\n"
        f"{pytest_line} \\\n"
        f" && npm i -g {pkg} \\\n"
        f" && {version_cmd} \\\n"
        " && apt-get clean \\\n"
        " && rm -rf /var/lib/apt/lists/*\n"
    )


def image_exists(tag: str) -> bool:
    proc = subprocess.run(
        ["docker", "image", "inspect", tag],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.returncode == 0


def build_image(
    base_image: str,
    agent: str = "opencode",
    tag: str | None = None,
    proxy: str | None = None,
    with_pytest: bool = True,
    force: bool = False,
) -> str:
    """Build (or reuse) a derived image and return its tag."""
    tag = tag or default_tag(base_image, agent)
    if not force and image_exists(tag):
        return tag

    dockerfile = dockerfile_for(base_image, agent, with_pytest)
    with tempfile.TemporaryDirectory() as tmp:
        df = Path(tmp) / "Dockerfile"
        df.write_text(dockerfile)
        cmd = ["docker", "build", "-t", tag, "-f", str(df), tmp]
        if proxy:
            for arg in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
                cmd += ["--build-arg", f"{arg}={proxy}"]
        subprocess.run(cmd, check=True)
    return tag
