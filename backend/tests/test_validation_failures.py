"""Failure attribution: new vs pre-existing, per test id rather than per command."""

import pytest

from patchquest.orchestrator.state_machine import _tool_available
from patchquest.validation import classify_failures, extract_failed_tests


@pytest.mark.parametrize("output, expected", [
    ("FAILED tests/test_a.py::test_x - assert 1 == 2\nERROR tests/test_b.py::test_y", {"tests/test_a.py::test_x", "tests/test_b.py::test_y"}),
    ("FAIL: test_add (test_calc.T.test_add)\nERROR: test_z (mod.C)", {"test_add.test_calc.T.test_add", "test_z.mod.C"}),
    ("--- FAIL: TestFoo (0.00s)\ntest a::b ... FAILED", {"TestFoo", "a::b"}),
    ("  ✕ renders the header (3 ms)\n  × other", {"renders the header", "other"}),
    ("all good", set()),
    ("", set()),
])
def test_extract_failed_tests(output, expected):
    assert extract_failed_tests(output) == expected


def result(cmd, out="", ok=False, code=1):
    return {"command": cmd, "success": ok, "returncode": code, "stdout": out, "stderr": ""}


def test_new_failure_in_an_already_red_suite_is_not_masked():
    cls = classify_failures(
        [result("pytest", "FAILED t.py::old\nFAILED t.py::new")],
        [result("pytest", "FAILED t.py::old")],
    )
    assert cls["pytest"] == {"new": ["t.py::new"], "preexisting": ["t.py::old"]}


def test_same_failures_are_preexisting():
    cls = classify_failures([result("pytest", "FAILED t.py::a")], [result("pytest", "FAILED t.py::a")])
    assert cls["pytest"]["new"] == []


def test_unparseable_failure_falls_back_to_exit_code_identity():
    cls = classify_failures([result("make", "boom", code=2)], [result("make", "bang", code=2)])
    assert cls["make"]["new"] == [] and cls["make"]["preexisting"] == ["<exit 2>"]
    cls = classify_failures([result("make", "boom", code=2)], [result("make", "ok", ok=True, code=0)])
    assert cls["make"]["new"] == ["<exit 2>"]


def test_passing_commands_are_ignored():
    assert classify_failures([result("pytest", ok=True, code=0)], []) == {}


def test_missing_tools_are_not_treated_as_test_failures():
    assert _tool_available("python3 -m unittest") is True
    assert _tool_available("definitely-not-installed --flag") is False
    assert _tool_available("unterminated 'quote") is False


def test_plain_test_layouts_are_detected(tmp_path):
    from patchquest.tools.test_runner import detect_test_commands

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_a():\n    pass\n")
    cmds = detect_test_commands(str(tmp_path))
    assert len(cmds) == 1 and ("pytest" in cmds[0] or "unittest discover" in cmds[0])
    empty = tmp_path / "nothing"
    empty.mkdir()
    assert detect_test_commands(str(empty)) == []
