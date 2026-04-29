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
