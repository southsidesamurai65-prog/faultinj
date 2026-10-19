"""Harness-agnostic bit-flip fault models.

Pure functions over UTF-8 bytes. No dependency on any agent, model, or runner.
The RNG mirrors the TypeScript adapter (mulberry32) so runs are reproducible
across the plugin and this reference pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

Mode = Literal["single", "burst"]


def _imul(a: int, b: int) -> int:
    return ((a & 0xFFFFFFFF) * (b & 0xFFFFFFFF)) & 0xFFFFFFFF


def mulberry32(seed: int) -> Callable[[], float]:
    """Deterministic PRNG identical to the opencode plugin's mulberry32."""
    a = seed & 0xFFFFFFFF

    def nxt() -> float:
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = _imul(a ^ (a >> 15), 1 | a)
        t = ((t + _imul(t ^ (t >> 7), 61 | t)) & 0xFFFFFFFF) ^ t
        return (((t ^ (t >> 14)) & 0xFFFFFFFF)) / 4294967296

    return nxt


@dataclass(frozen=True)
class FaultConfig:
    """Fault model parameters.

    single: every bit independently flips with probability ``prob``.
    burst:  each byte triggers with probability ``prob``; a run of ``width``
            consecutive bytes is corrupted, and **all 8 bits of every byte in
            the run are flipped** (memory-row style full-byte corruption).
    """

    mode: Mode = "single"
    prob: float = 0.0
    width: int = 8
    seed: int = 1


@dataclass
class Flip:
    offset: int
    bit: int


@dataclass
class Faulted:
    text: str
    flips: list[Flip] = field(default_factory=list)

    @property
    def n_flips(self) -> int:
        return len(self.flips)


def apply_fault(text: str, cfg: FaultConfig, rng: Callable[[], float]) -> Faulted:
    """Return ``text`` with bit flips applied according to ``cfg``."""
    buf = bytearray(text.encode("utf-8"))
    flips: list[Flip] = []

    if cfg.mode == "burst":
        offsets: set[int] = set()
        i = 0
        n = len(buf)
        while i < n:
            if rng() < cfg.prob:
                end = min(i + cfg.width, n)
                offsets.update(range(i, end))
                i = end
            else:
                i += 1
        for o in sorted(offsets):
            buf[o] ^= 0xFF
            for b in range(8):
                flips.append(Flip(o, b))
    else:
        for i in range(len(buf)):
            for b in range(8):
                if rng() < cfg.prob:
                    buf[i] ^= 1 << b
                    flips.append(Flip(i, b))

    return Faulted(buf.decode("utf-8", errors="replace"), flips)
