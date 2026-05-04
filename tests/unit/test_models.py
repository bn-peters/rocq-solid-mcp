from __future__ import annotations

from pathlib import Path

from rocq_lsp_mcp.config import DEFAULT_MESSAGE_LIMIT, MAX_MESSAGE_LIMIT
from rocq_lsp_mcp.models import (
    RunAtResult,
    CheckResult,
    Diagnostic,
    LongRunningCommand,
    RenderOptions,
    TimeoutContext,
    long_running_commands_from_perf_data,
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


def test_check_result_renders_nonzero_counts(tmp_path: Path) -> None:
    source = tmp_path / "src" / "Demo.v"
    source.parent.mkdir()
    source.write_text("Check nat.\n", encoding="utf-8")
    result = CheckResult.from_diagnostics(
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
        "counts": {"errors": 1},
        "diagnostics": [{"level": "error", "message": "bad"}],
    }


def test_check_result_counts_omit_zero_fields(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    result = CheckResult.from_diagnostics(
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

    assert "counts" not in result.render()
    assert result.render(RenderOptions(severity="info"))["counts"] == {"warnings": 1}


def test_check_result_omits_counts_without_diagnostics(tmp_path: Path) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    result = CheckResult.from_diagnostics(
        status="complete",
        file=str(source),
        uri=source.as_uri(),
        workspace=str(tmp_path),
        version=1,
        completed=None,
        diagnostics=[],
    )

    assert result.render() == {
        "status": "complete",
        "file": "Demo.v",
    }


def test_check_result_renders_checking_time_and_long_running_commands(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Goal True.\nProof.\n  sleep 5.\nQed.\n", encoding="utf-8")
    result = CheckResult.from_diagnostics(
        status="complete",
        file=str(source),
        uri=source.as_uri(),
        workspace=str(tmp_path),
        version=1,
        completed=None,
        diagnostics=[],
        checking_time_seconds=1.23456,
        long_running_commands=[
            LongRunningCommand(
                range={
                    "start": {"line": 2, "character": 2},
                    "end": {"line": 2, "character": 10},
                },
                text="sleep 5.",
                time_seconds=5.0,
            )
        ],
    )

    assert result.render() == {
        "status": "complete",
        "file": "Demo.v",
        "checking_time_seconds": 1.235,
        "long_running_commands": [
            {
                "location": "3:3 to 3:11",
                "text": "sleep 5.",
            }
        ],
    }


def test_long_running_commands_from_perf_data_filters_cached_and_caps() -> None:
    content = "\n".join(f"  sleep {index}." for index in range(12)) + "\n"
    timings = []
    for index in range(12):
        timings.append(
            {
                "range": {
                    "start": {"line": index, "character": 2},
                    "end": {"line": index, "character": len(f"  sleep {index}.")},
                },
                "info": {
                    "time": 4.0,
                    "memory": 0,
                    "cache_hit": index == 1,
                    "time_hash": 0.0,
                },
            }
        )
    timings[0]["info"]["time"] = 2.0

    commands = long_running_commands_from_perf_data(
        content=content,
        perf_data={
            "textDocument": {"uri": "file:///Demo.v", "version": 1},
            "summary": "",
            "timings": timings,
        },
        threshold_seconds=3,
    )

    assert len(commands) == 10
    assert [command.text for command in commands[:2]] == ["sleep 2.", "sleep 3."]
    assert [command.text for command in commands[-2:]] == ["sleep 10.", "sleep 11."]

    executed_only = long_running_commands_from_perf_data(
        content=content,
        perf_data={
            "textDocument": {"uri": "file:///Demo.v", "version": 1},
            "summary": "",
            "timings": timings,
        },
        threshold_seconds=3,
        executed_ranges=[
            {
                "start": {"line": 4, "character": 2},
                "end": {"line": 4, "character": len("  sleep 4.")},
            }
        ],
    )
    assert [command.text for command in executed_only] == ["sleep 4."]


def test_check_result_renders_timeout_context_only_for_timeouts(
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

    timeout_result = CheckResult.from_diagnostics(
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
    complete_result = CheckResult.from_diagnostics(
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


def test_check_result_limits_errors_by_default(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    diagnostics = [
        *[
            Diagnostic.from_lsp(
                {"range": {}, "message": f"bad {index}", "severity": 1}
            ).model_copy(update={"proof_name": f"proof_{index}"})
            for index in range(22)
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

    result = CheckResult.from_diagnostics(
        status="complete",
        file=str(source),
        uri=source.as_uri(),
        workspace=str(tmp_path),
        version=1,
        completed=None,
        diagnostics=diagnostics,
    )

    dumped = result.render()
    assert len(dumped["diagnostics"]) == DEFAULT_MESSAGE_LIMIT
    assert dumped["counts"] == {"errors": 22}
    assert dumped["omitted_counts"] == {"errors": 2}
    assert dumped["message_limit"] == DEFAULT_MESSAGE_LIMIT
    assert [diagnostic["message"] for diagnostic in dumped["diagnostics"]] == [
        *[f"bad {index}" for index in range(DEFAULT_MESSAGE_LIMIT)],
    ]
    assert result.error_count == 22
    assert len(result.errors) == 22


def test_check_result_hard_caps_requested_message_limit(tmp_path: Path) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")

    result = CheckResult.from_diagnostics(
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
    assert dumped["message_limit"] == MAX_MESSAGE_LIMIT


def test_check_result_filters_diagnostics_by_severity(tmp_path: Path) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    result = CheckResult.from_diagnostics(
        status="complete",
        file=str(source),
        uri=source.as_uri(),
        workspace=str(tmp_path),
        version=1,
        completed=None,
        diagnostics=[
            Diagnostic.from_lsp({"range": {}, "message": "bad", "severity": 1}),
            Diagnostic.from_lsp({"range": {}, "message": "careful", "severity": 2}),
            Diagnostic.from_lsp({"range": {}, "message": "note", "severity": 3}),
            Diagnostic.from_lsp({"range": {}, "message": "hint", "severity": 4}),
            Diagnostic.from_lsp({"range": {}, "message": "unknown"}),
        ],
    )

    default_dumped = result.render()
    assert default_dumped["counts"] == {"errors": 1}
    assert [item["message"] for item in default_dumped["diagnostics"]] == ["bad"]

    warning_dumped = result.render(RenderOptions(severity="warning"))
    assert warning_dumped["counts"] == {"errors": 1, "warnings": 1}
    assert [item["message"] for item in warning_dumped["diagnostics"]] == [
        "bad",
        "careful",
    ]

    error_dumped = result.render(RenderOptions(severity="error"))
    assert error_dumped["counts"] == {"errors": 1}
    assert [item["message"] for item in error_dumped["diagnostics"]] == ["bad"]


def test_check_result_render_deduplicates_proof_errors(tmp_path: Path) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    result = CheckResult.from_diagnostics(
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
    ]
    assert [
        item["message"]
        for item in result.render(RenderOptions(severity="info"))["diagnostics"]
    ] == ["first foo", "foo warning"]
    assert [
        item["message"]
        for item in result.render(
            RenderOptions(deduplicate_proof_errors=False)
        )["diagnostics"]
    ] == ["first foo", "second foo"]
    assert [
        item["message"]
        for item in result.render(
            RenderOptions(severity="info", deduplicate_proof_errors=False)
        )["diagnostics"]
    ] == ["first foo", "second foo", "foo warning"]
    assert result.error_count == 2


def test_check_result_render_suppresses_later_global_errors(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    result = CheckResult.from_diagnostics(
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
        "proof error",
    ]
    assert [
        item["message"]
        for item in result.render(RenderOptions(severity="info"))["diagnostics"]
    ] == [
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
        "proof error",
    ]
    assert result.error_count == 3


def test_check_result_global_error_suppression_is_file_wide_for_now(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    result = CheckResult.from_diagnostics(
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


def test_run_at_result_renders_agent_facing_fields(tmp_path: Path) -> None:
    source = tmp_path / "src" / "Demo.v"
    source.parent.mkdir()
    source.write_text("Check nat.\n", encoding="utf-8")
    result = RunAtResult.from_feedback(
        status="complete",
        file=str(source),
        commands="Search (_ + 0 = _).",
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
        "commands": "Search (_ + 0 = _).",
        "output": [
            {
                "level": "info",
                "text": "Nat.add_0_r: forall n : nat, n + 0 = n",
            }
        ],
    }
