import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipe.benchmarks import (  # noqa: E402
    CodeQA,
    KnowledgeQA,
    LongContextQA,
    MathQA,
    choice_correct,
    extract_final_answer,
    math_correct,
)


def test_extract_and_grade():
    assert extract_final_answer("blah\nAnswer: 18") == "18"
    assert extract_final_answer("so \\boxed{42}") == "42"
    assert math_correct("18", "18")
    assert math_correct("18.0", "18")
    assert not math_correct("17", "18")
    assert choice_correct("C", "C")
    assert choice_correct("Answer: C", "C")
    assert not choice_correct("A", "C")


def test_pilot_loaders():
    base = Path(__file__).resolve().parent.parent.parent / "data" / "pilot"
    math = MathQA(base / "math.jsonl").tasks()
    math30 = MathQA(base / "math30.jsonl").tasks()
    know = KnowledgeQA(base / "knowledge.jsonl").tasks()
    longctx = LongContextQA(base / "longcontext.jsonl").tasks()
    code = CodeQA(base.parent / "code" / "humaneval3.jsonl").tasks()
    assert len(math) == 5 and len(math30) == 30 and len(know) == 5
    assert len(longctx) == 3 and len(code) == 3
    assert math[0].answer and "Answer:" in math[0].instruction
    assert know[0].metadata["choices"]
    assert longctx[0].metadata["context_chars"] > 1000
