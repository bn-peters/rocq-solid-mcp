from __future__ import annotations

from pathlib import Path

from rocq_lsp_mcp.models import CommandResult, CompileResult, Diagnostic


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


def test_compile_result_limits_diagnostics_per_level_by_default(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    diagnostics = [
        *[
            Diagnostic.from_lsp(
                {"range": {}, "message": f"bad {index}", "severity": 1}
            )
            for index in range(12)
        ],
        *[
            Diagnostic.from_lsp(
                {"range": {}, "message": f"careful {index}", "severity": 2}
            )
            for index in range(11)
        ],
        *[
            Diagnostic.from_lsp(
                {"range": {}, "message": f"note {index}", "severity": 3}
            )
            for index in range(13)
        ],
    ]

    result = CompileResult.from_diagnostics(
        status="complete",
        file=str(source),
        uri=source.as_uri(),
        workspace=str(tmp_path),
        version=1,
        completed=None,
        diagnostics=diagnostics,
    )

    dumped = result.model_dump()
    assert len(dumped["diagnostics"]) == 30
    assert dumped["counts"] == {"errors": 12, "warnings": 11, "infos": 13}
    assert dumped["omitted_counts"] == {"errors": 2, "warnings": 1, "infos": 3}
    assert dumped["message_limit"] == 10
    assert [diagnostic["message"] for diagnostic in dumped["diagnostics"]] == [
        *[f"bad {index}" for index in range(10)],
        *[f"careful {index}" for index in range(10)],
        *[f"note {index}" for index in range(10)],
    ]
    assert result.error_count == 12
    assert len(result.errors) == 10


def test_compile_result_hard_caps_requested_message_limit(tmp_path: Path) -> None:
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
                {"range": {}, "message": f"bad {index}", "severity": 1}
            )
            for index in range(51)
        ],
        message_limit=1000,
    )

    dumped = result.model_dump()
    assert len(dumped["diagnostics"]) == 50
    assert dumped["counts"] == {"errors": 51}
    assert dumped["omitted_counts"] == {"errors": 1}
    assert dumped["message_limit"] == 50


def test_command_result_serializes_agent_facing_fields(tmp_path: Path) -> None:
    source = tmp_path / "src" / "Demo.v"
    source.parent.mkdir()
    source.write_text("Check nat.\n", encoding="utf-8")
    result = CommandResult.from_feedback(
        status="complete",
        file=str(source),
        command="Search (_ + 0 = _).",
        location="1:1",
        uri=source.as_uri(),
        workspace=str(tmp_path),
        version=1,
        feedback={"feedback": [[3, "Nat.add_0_r: forall n : nat, n + 0 = n"]]},
    )

    assert result.model_dump() == {
        "status": "complete",
        "file": "src/Demo.v",
        "location": "1:1",
        "command": "Search (_ + 0 = _).",
        "output": [
            {
                "level": "info",
                "text": "Nat.add_0_r: forall n : nat, n + 0 = n",
            }
        ],
    }
