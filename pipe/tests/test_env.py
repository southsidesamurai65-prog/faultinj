import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipe import load_env, parse_env_text  # noqa: E402


def test_parse_env_text():
    values = parse_env_text(
        "# comment\n"
        "export A=1\n"
        'B="two"\n'
        "C='three'\n"
        "EMPTY=\n"
        "no_equals_line\n"
    )
    assert values == {"A": "1", "B": "two", "C": "three", "EMPTY": ""}


def test_load_env_does_not_override(tmp_path, monkeypatch):
    env_file = tmp_path / "x.env"
    env_file.write_text("PIPE_TEST_NEW=abc\nPIPE_TEST_EXISTING=fromfile\n")
    monkeypatch.delenv("PIPE_TEST_NEW", raising=False)
    monkeypatch.setenv("PIPE_TEST_EXISTING", "already")
    loaded = load_env(env_file)
    assert os.environ["PIPE_TEST_NEW"] == "abc"
    assert os.environ["PIPE_TEST_EXISTING"] == "already"
    assert loaded["PIPE_TEST_NEW"] == "abc"


def test_load_env_missing_file_is_noop(tmp_path):
    assert load_env(tmp_path / "nope.env") == {}
