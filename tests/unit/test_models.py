from __future__ import annotations

from pathlib import Path

from rocq_lsp_mcp.models import (
    CommandResult,
    CompileResult,
    Diagnostic,
    RenderOptions,
    TimeoutContext,
)


def test_diagnostic_renders_agent_facing_fields() -> None:
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

    assert diagnostic.render() == {
        "level": "error",
        "message": "Tactic failure.",
        "location": "1:3 to 1:8",
        "proof_name": "foo",
        "proof_state": {"goals": [{"conclusion": "True"}]},
    }
    assert diagnostic.model_dump()["range"] == {
        "start": {"line": 0, "character": 2},
        "end": {"line": 0, "character": 7},
    }


def test_diagnostic_render_options_control_proof_state_and_location() -> None:
    diagnostic = Diagnostic.from_lsp(
        {
            "range": {
                "start": {"line": 0, "character": 2},
                "end": {"line": 0, "character": 7},
            },
            "message": "Tactic failure.",
            "severity": 1,
        },
        proof_state={"goals": [{"conclusion": "True"}]},
    )

    assert diagnostic.render(
        RenderOptions(include_proof_state=False, location_format="point")
    ) == {
        "level": "error",
        "message": "Tactic failure.",
        "location": "1:3",
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

    assert result.render() == {
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

    assert result.render()["counts"] == {"warnings": 1}


def test_compile_result_renders_timeout_context_only_for_timeouts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    timeout_context = TimeoutContext(
        request_timeout_seconds=1,
        cancel_grace_seconds=1,
        checked_until={"line": 1, "character": 2},
        next_unchecked={"location": "3:1", "line": "Check nat."},
    )

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

    assert timeout_result.render()["timeout_context"] == {
        "operation": "coq/getDocument",
        "timeout_kind": "request",
        "request_timeout_seconds": 1,
        "cancel_grace_seconds": 1,
        "checked_until": "2:3",
        "next_unchecked": {"location": "3:1", "line": "Check nat."},
    }
    assert "timeout_context" not in complete_result.render()
    assert "timeout_context" not in timeout_result.render(
        RenderOptions(include_timeout_context=False)
    )


def test_timeout_context_builds_from_manager_state() -> None:
    context = TimeoutContext.from_manager_state(
        content="Check nat.\nCheck bool.\n",
        document={
            "completed": {
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 10},
                }
            }
        },
        progress={
            "processing": [
                {
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 1, "character": 11},
                    }
                }
            ]
        },
        request_timeout_seconds=1,
        cancel_grace_seconds=0.5,
        timeout_kind="command",
        command_timeout_seconds=2,
        timed_out_range={
            "start": {"line": 1, "character": 0},
            "end": {"line": 1, "character": 11},
        },
    )

    assert context.render() == {
        "operation": "coq/getDocument",
        "timeout_kind": "command",
        "request_timeout_seconds": 1,
        "cancel_grace_seconds": 0.5,
        "checked_until": "1:11",
        "latest_processing_range": "1:1 to 2:12",
        "next_unchecked": {"location": "2:1", "line": "Check bool."},
        "command_timeout_seconds": 2,
        "timed_out_range": "2:1 to 2:12",
        "timed_out_command": {"location": "2:1", "line": "Check bool."},
    }


def test_compile_result_limits_diagnostics_per_level_by_default(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    diagnostics = [
        *[
            Diagnostic.from_lsp(
                {"range": {}, "message": f"bad {index}", "severity": 1}
            ).model_copy(update={"proof_name": f"proof_{index}"})
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

    dumped = result.render()
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
    assert len(result.errors) == 12


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
            ).model_copy(update={"proof_name": f"proof_{index}"})
            for index in range(51)
        ],
    )

    dumped = result.render(RenderOptions(message_limit=1000))
    assert len(dumped["diagnostics"]) == 50
    assert dumped["counts"] == {"errors": 51}
    assert dumped["omitted_counts"] == {"errors": 1}
    assert dumped["message_limit"] == 50


def test_compile_result_render_deduplicates_proof_errors(tmp_path: Path) -> None:
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
                {"range": {}, "message": "first foo", "severity": 1},
                proof_state=None,
            ).model_copy(update={"proof_name": "foo"}),
            Diagnostic.from_lsp(
                {"range": {}, "message": "second foo", "severity": 1},
                proof_state=None,
            ).model_copy(update={"proof_name": "foo"}),
            Diagnostic.from_lsp(
                {"range": {}, "message": "foo warning", "severity": 2},
                proof_state=None,
            ).model_copy(update={"proof_name": "foo"}),
        ],
    )

    assert [item["message"] for item in result.render()["diagnostics"]] == [
        "first foo",
        "foo warning",
    ]
    assert [
        item["message"]
        for item in result.render(
            RenderOptions(deduplicate_proof_errors=False)
        )["diagnostics"]
    ] == ["first foo", "second foo", "foo warning"]
    assert result.error_count == 2


def test_compile_result_render_suppresses_later_global_errors(
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
                {"range": {}, "message": "first global", "severity": 1},
            ),
            Diagnostic.from_lsp(
                {"range": {}, "message": "global cascade", "severity": 1},
            ),
            Diagnostic.from_lsp(
                {"range": {}, "message": "global warning", "severity": 2},
            ),
            Diagnostic.from_lsp(
                {"range": {}, "message": "proof error", "severity": 1},
            ).model_copy(update={"proof_name": "foo"}),
        ],
    )

    assert [item["message"] for item in result.render()["diagnostics"]] == [
        "first global",
        "global warning",
        "proof error",
    ]
    assert [
        item["message"]
        for item in result.render(
            RenderOptions(deduplicate_proof_errors=False)
        )["diagnostics"]
    ] == [
        "first global",
        "global cascade",
        "global warning",
        "proof error",
    ]
    assert result.error_count == 3


def test_compile_result_global_error_suppression_is_file_wide_for_now(
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
                {"range": {}, "message": "bad lemma header 1", "severity": 1},
            ),
            Diagnostic.from_lsp(
                {"range": {}, "message": "bad lemma header 2", "severity": 1},
            ),
        ],
    )

    assert [item["message"] for item in result.render()["diagnostics"]] == [
        "bad lemma header 1",
    ]
    assert result.error_count == 2


def test_command_result_renders_agent_facing_fields(tmp_path: Path) -> None:
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

    assert result.render() == {
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
