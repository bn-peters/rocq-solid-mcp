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
