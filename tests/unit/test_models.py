from __future__ import annotations

from pathlib import Path

from rocq_lsp_mcp.models import CompileResult, Diagnostic


def test_diagnostic_serializes_agent_facing_fields() -> None:
    diagnostic = Diagnostic.from_lsp(
        {
            "range": {
                "start": {"line": 0, "character": 2},
                "end": {"line": 0, "character": 7},
            },
            "message": "Tactic failure.",
            "severity": 1,
            "data": {"proofName": "foo"},
        },
        proof_state={"goals": [{"conclusion": "True"}]},
    )

    assert diagnostic.model_dump() == {
        "level": "error",
        "message": "Tactic failure.",
        "location": "1:3 to 1:8",
        "proof_name": "foo",
        "proof_state": {"goals": [{"conclusion": "True"}]},
    }


def test_compile_result_omits_counts_without_warnings(tmp_path: Path) -> None:
    source = tmp_path / "src" / "Demo.v"
    source.parent.mkdir()
    source.write_text("Check nat.\n", encoding="utf-8")
    result = CompileResult.from_diagnostics(
        status="complete",
        file=str(source),
        uri=source.as_uri(),
        workspace=str(tmp_path),
        version=17,
        completed=None,
        diagnostics=[
            Diagnostic.from_lsp(
                {"range": {}, "message": "bad", "severity": 1},
            )
        ],
    )

    assert result.model_dump() == {
        "status": "complete",
        "file": "src/Demo.v",
        "diagnostics": [{"level": "error", "message": "bad"}],
    }


def test_compile_result_counts_keep_only_nonzero_fields_for_warnings(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    result = CompileResult.from_diagnostics(
        status="complete",
        file=str(source),
        uri=source.as_uri(),
        workspace=str(tmp_path),
        version=1,
        completed=None,
        diagnostics=[
            Diagnostic.from_lsp(
                {"range": {}, "message": "careful", "severity": 2},
            )
        ],
    )

    assert result.model_dump()["counts"] == {"warnings": 1}


def test_compile_result_serializes_timeout_context_only_for_timeouts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    timeout_context = {
        "operation": "coq/getDocument",
        "request_timeout_seconds": 1,
        "cancel_grace_seconds": 1,
        "checked_until": "2:3",
        "latest_processing_range": None,
        "next_unchecked": {"location": "3:1", "line": "Check nat."},
    }

    timeout_result = CompileResult.from_diagnostics(
        status="timeout",
        file=str(source),
        uri=source.as_uri(),
        workspace=str(tmp_path),
        version=1,
        completed=None,
        diagnostics=[],
        timed_out=True,
        timeout_context=timeout_context,
    )
    complete_result = CompileResult.from_diagnostics(
        status="complete",
        file=str(source),
        uri=source.as_uri(),
        workspace=str(tmp_path),
        version=1,
        completed=None,
        diagnostics=[],
        timeout_context=timeout_context,
    )

    assert timeout_result.model_dump()["timeout_context"] == {
        "operation": "coq/getDocument",
        "request_timeout_seconds": 1,
        "cancel_grace_seconds": 1,
        "checked_until": "2:3",
        "next_unchecked": {"location": "3:1", "line": "Check nat."},
    }
    assert "timeout_context" not in complete_result.model_dump()
