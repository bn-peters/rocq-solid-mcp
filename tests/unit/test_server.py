from __future__ import annotations

from inspect import signature
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args, get_type_hints

import pytest
from pydantic.fields import FieldInfo

from rocq_lsp_mcp.models import RunAtResult, CheckResult, Diagnostic
from rocq_lsp_mcp.server import rocq_lsp_check_file, rocq_lsp_run_at


class FakeManager:
    def __init__(
        self,
        result: CheckResult,
        run_at_result: RunAtResult | None = None,
        message_limit: int = 20,
    ) -> None:
        self.result = result
        self.run_at_result = run_at_result
        self.config = SimpleNamespace(message_limit=message_limit)
        self.check_kwargs: dict[str, Any] | None = None
        self.run_at_calls: list[dict[str, Any]] = []

    async def check_file(self, file: str, **kwargs: Any) -> CheckResult:
        self.check_kwargs = {"file": file, **kwargs}
        return self.result

    async def run_at(
        self,
        file: str,
        commands: str,
        line: int,
        character: int,
        **kwargs: Any,
    ) -> RunAtResult:
        self.run_at_calls.append(
            {
                "file": file,
                "commands": commands,
                "line": line,
                "character": character,
                **kwargs,
            }
        )
        assert self.run_at_result is not None
        return self.run_at_result


@pytest.mark.asyncio
async def test_check_tool_reports_errors_only_with_configured_limit(
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
                {"range": {}, "message": "bad 1", "severity": 1},
            ).model_copy(update={"proof_name": "one"}),
            Diagnostic.from_lsp(
                {"range": {}, "message": "bad 2", "severity": 1},
            ).model_copy(update={"proof_name": "two"}),
            Diagnostic.from_lsp({"range": {}, "message": "careful", "severity": 2}),
            Diagnostic.from_lsp({"range": {}, "message": "note", "severity": 3}),
        ],
    )
    manager = FakeManager(result, message_limit=1)
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=manager),
    )

    dumped = await rocq_lsp_check_file(
        ctx=ctx,
        file=str(source),
    )

    assert manager.check_kwargs == {
        "file": str(source),
        "overall_timeout_seconds": 60.0,
        "command_timeout_seconds": None,
        "long_running_seconds": 48.0,
        "restart": False,
    }
    assert [item["message"] for item in dumped["diagnostics"]] == ["bad 1"]
    assert dumped["counts"] == {"errors": 2}
    assert dumped["omitted_counts"] == {"errors": 1}
    assert dumped["message_limit"] == 1
    assert "severity" not in signature(rocq_lsp_check_file).parameters
    assert "message_limit" not in signature(rocq_lsp_check_file).parameters


@pytest.mark.asyncio
async def test_run_at_tool_passes_commands_string(tmp_path: Path) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    check_result = CheckResult.from_diagnostics(
        status="complete",
        file=str(source),
        uri=source.as_uri(),
        workspace=str(tmp_path),
        version=1,
        completed=None,
        diagnostics=[],
    )
    run_at_result = RunAtResult.from_feedback(
        status="complete",
        file=str(source),
        commands="Require Import Arith. Check Nat.add. Search (_ + _).",
        location="1:2",
        uri=source.as_uri(),
        workspace=str(tmp_path),
        version=1,
        feedback={
            "feedback": [
                [3, "Nat.add : nat -> nat -> nat"],
                [3, "Nat.add_comm: forall n m, n + m = m + n"],
            ]
        },
    )
    manager = FakeManager(check_result, run_at_result=run_at_result)
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=manager),
    )

    dumped = await rocq_lsp_run_at(
        ctx=ctx,
        file=str(source),
        commands="Require Import Arith. Check Nat.add. Search (_ + _).",
        line=1,
        character=2,
        overall_timeout_seconds=2.5,
    )

    assert manager.run_at_calls == [
        {
            "file": str(source),
            "commands": "Require Import Arith. Check Nat.add. Search (_ + _).",
            "line": 1,
            "character": 2,
            "overall_timeout_seconds": 2.5,
        }
    ]
    assert dumped["status"] == "complete"
    assert dumped["file"] == "Demo.v"
    assert dumped["location"] == "1:2"
    assert dumped["commands"] == "Require Import Arith. Check Nat.add. Search (_ + _)."
    assert [item["text"] for item in dumped["output"]] == [
        "Nat.add : nat -> nat -> nat",
        "Nat.add_comm: forall n m, n + m = m + n",
    ]


@pytest.mark.asyncio
async def test_run_at_tool_renders_warning_feedback(tmp_path: Path) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    check_result = CheckResult.from_diagnostics(
        status="complete",
        file=str(source),
        uri=source.as_uri(),
        workspace=str(tmp_path),
        version=1,
        completed=None,
        diagnostics=[],
    )
    run_at_result = RunAtResult.from_feedback(
        status="complete",
        file=str(source),
        commands="Definition f x := match x with | true => true | y => false end.",
        location="1:1",
        uri=source.as_uri(),
        workspace=str(tmp_path),
        version=1,
        feedback={
            "feedback": [
                [
                    2,
                    "Unused variable y might be a misspelled constructor.",
                ]
            ]
        },
    )
    manager = FakeManager(check_result, run_at_result=run_at_result)
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=manager),
    )

    dumped = await rocq_lsp_run_at(
        ctx=ctx,
        file=str(source),
        commands="Definition f x := match x with | true => true | y => false end.",
        line=1,
    )

    assert dumped["output"] == [
        {
            "level": "warning",
            "text": "Unused variable y might be a misspelled constructor.",
        }
    ]


def test_timeout_tool_descriptions_distinguish_soft_and_host_timeouts() -> None:
    check_description = _field_description(
        rocq_lsp_check_file, "overall_timeout_seconds"
    )
    command_description = _field_description(
        rocq_lsp_check_file, "command_timeout_seconds"
    )
    run_at_description = _field_description(
        rocq_lsp_run_at, "overall_timeout_seconds"
    )

    assert "soft coq-lsp request deadline" in check_description.lower()
    assert "MCP host cancels" in check_description
    assert "partial diagnostics" in check_description
    assert "per Rocq sentence watchdog" in command_description
    assert "slow sentence" in command_description
    assert "soft coq-lsp request deadline" in run_at_description.lower()
    assert "MCP host cancels" in run_at_description


def _field_description(function: Any, parameter: str) -> str:
    annotation = get_type_hints(function, include_extras=True)[parameter]
    for item in get_args(annotation):
        if isinstance(item, FieldInfo):
            return item.description or ""
    raise AssertionError(f"{parameter} has no FieldInfo")
