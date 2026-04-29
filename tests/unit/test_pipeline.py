from __future__ import annotations

from typing import Any

from rocq_lsp_mcp.pipeline import build_diagnostics


def test_build_diagnostics_attaches_proof_state_by_sentence_range() -> None:
    range_ = _range(0, 5)
    diagnostics = [
        {
            "range": _range(2, 3),
            "message": "bad",
            "severity": 1,
            "data": {"sentenceRange": range_, "proofName": "foo"},
        }
    ]
    document = {"spans": [{"range": range_, "goals": {"goals": [], "messages": []}}]}

    result = build_diagnostics(diagnostics, document)

    assert result[0].proof_name == "foo"
    assert result[0].proof_state == {"goals": [], "messages": []}


def test_build_diagnostics_falls_back_to_diagnostic_range() -> None:
    range_ = _range(4, 8)
    diagnostics = [{"range": range_, "message": "bad", "severity": 1}]
    document = {"spans": [{"range": range_, "goals": {"goals": []}}]}

    result = build_diagnostics(diagnostics, document)

    assert result[0].proof_state == {"goals": []}


def test_build_diagnostics_filters_duplicate_proof_errors_only() -> None:
    diagnostics = [
        _diagnostic("first foo", 1, proof_name="foo", line=0),
        _diagnostic("second foo", 1, proof_name="foo", line=1),
        _diagnostic("foo warning", 2, proof_name="foo", line=2),
        _diagnostic("first bar", 1, proof_name="bar", line=3),
        _diagnostic("global first", 1, line=4),
        _diagnostic("global second", 1, line=5),
    ]

    result = build_diagnostics(diagnostics, None)

    assert [diagnostic.message for diagnostic in result] == [
        "first foo",
        "foo warning",
        "first bar",
        "global first",
        "global second",
    ]


def _diagnostic(
    message: str,
    severity: int,
    *,
    proof_name: str | None = None,
    line: int = 0,
) -> dict[str, Any]:
    range_ = {
        "start": {"line": line, "character": 0},
        "end": {"line": line, "character": 1},
    }
    result: dict[str, Any] = {
        "range": range_,
        "message": message,
        "severity": severity,
    }
    if proof_name is not None:
        result["data"] = {"sentenceRange": range_, "proofName": proof_name}
    return result


def _range(start_character: int, end_character: int) -> dict[str, object]:
    return {
        "start": {"line": 0, "character": start_character},
        "end": {"line": 0, "character": end_character},
    }
