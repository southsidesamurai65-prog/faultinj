import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipe import FaultConfig, apply_fault, mulberry32  # noqa: E402


def test_no_flips_when_prob_zero():
    res = apply_fault("hello world", FaultConfig(prob=0.0), mulberry32(1))
    assert res.n_flips == 0
    assert res.text == "hello world"


def test_determinism_same_seed():
    cfg = FaultConfig(mode="single", prob=0.1, seed=7)
    a = apply_fault("the quick brown fox" * 5, cfg, mulberry32(cfg.seed))
    b = apply_fault("the quick brown fox" * 5, cfg, mulberry32(cfg.seed))
    assert a.text == b.text
    assert a.n_flips == b.n_flips


def test_burst_flips_whole_bytes():
    text = "abcdefghij"
    cfg = FaultConfig(mode="burst", prob=1.0, width=4, seed=1)
    res = apply_fault(text, cfg, mulberry32(cfg.seed))
    # every byte is corrupted, each with all 8 bits flipped
    assert {f.offset for f in res.flips} == set(range(len(text)))
    assert res.n_flips == 8 * len(text)
    counts: dict[int, int] = {}
    for f in res.flips:
        counts[f.offset] = counts.get(f.offset, 0) + 1
    assert all(v == 8 for v in counts.values())
    # bitwise NOT of each byte
    expected = bytes(b ^ 0xFF for b in text.encode()).decode("utf-8", errors="replace")
    assert res.text == expected


def test_burst_width_respected():
    text = "x" * 100
    width = 8
    cfg = FaultConfig(mode="burst", prob=0.2, width=width, seed=3)
    res = apply_fault(text, cfg, mulberry32(cfg.seed))
    offsets = sorted({f.offset for f in res.flips})

    runs: list[tuple[int, int]] = []
    for o in offsets:
        if runs and o == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], o)
        else:
            runs.append((o, o))

    for start, end in runs:
        length = end - start + 1
        # adjacent bursts concatenate, so lengths are multiples of width,
        # except a final burst truncated by the end of the buffer.
        assert length % width == 0 or end == len(text) - 1
