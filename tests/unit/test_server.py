from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args, get_type_hints

import pytest
from pydantic.fields import FieldInfo

from rocq_lsp_mcp.models import CommandResult, CompileResult, Diagnostic
from rocq_lsp_mcp.server import rocq_lsp_compile_file, rocq_lsp_run_command


class FakeManager:
    def __init__(
        self,
        result: CompileResult,
        command_results: list[CommandResult] | None = None,
    ) -> None:
        self.result = result
        self.command_results = command_results or []
        self.compile_kwargs: dict[str, Any] | None = None
        self.command_calls: list[dict[str, Any]] = []

    async def compile_file(self, file: str, **kwargs: Any) -> CompileResult:
        self.compile_kwargs = {"file": file, **kwargs}
        return self.result

    async def run_command(
        self,
        file: str,
        command: str,
        line: int,
        character: int,
        **kwargs: Any,
    ) -> CommandResult:
        self.command_calls.append(
            {
                "file": file,
                "command": command,
                "line": line,
                "character": character,
                **kwargs,
            }
        )
        return self.command_results[len(self.command_calls) - 1]


@pytest.mark.asyncio
async def test_compile_tool_applies_severity_filter(tmp_path: Path) -> None:
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
            Diagnostic.from_lsp({"range": {}, "message": "bad", "severity": 1}),
            Diagnostic.from_lsp({"range": {}, "message": "careful", "severity": 2}),
            Diagnostic.from_lsp({"range": {}, "message": "note", "severity": 3}),
        ],
    )
    manager = FakeManager(result)
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=manager),
    )

    dumped = await rocq_lsp_compile_file(
        ctx=ctx,
        file=str(source),
        severity="warning",
    )

    assert manager.compile_kwargs == {
        "file": str(source),
        "overall_timeout_seconds": 60.0,
        "command_timeout_seconds": None,
        "long_running_seconds": 48.0,
        "restart": False,
    }
    assert [item["message"] for item in dumped["diagnostics"]] == [
        "bad",
        "careful",
    ]


@pytest.mark.asyncio
async def test_run_command_tool_executes_command_batch(tmp_path: Path) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    compile_result = CompileResult.from_diagnostics(
        status="complete",
        file=str(source),
        uri=source.as_uri(),
        workspace=str(tmp_path),
        version=1,
        completed=None,
        diagnostics=[],
    )
    command_results = [
        CommandResult.from_feedback(
            status="complete",
            file=str(source),
            command="Check Nat.add.",
            location="1:2",
            uri=source.as_uri(),
            workspace=str(tmp_path),
            version=1,
            feedback={"feedback": [[3, "Nat.add : nat -> nat -> nat"]]},
        ),
        CommandResult.from_feedback(
            status="complete",
            file=str(source),
            command="Search (_ + _).",
            location="1:2",
            uri=source.as_uri(),
            workspace=str(tmp_path),
            version=2,
            feedback={"feedback": [[3, "Nat.add_comm: forall n m, n + m = m + n"]]},
        ),
    ]
    manager = FakeManager(compile_result, command_results=command_results)
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=manager),
    )

    dumped = await rocq_lsp_run_command(
        ctx=ctx,
        file=str(source),
        commands=["Check Nat.add.", "Search (_ + _)."],
        line=1,
        character=2,
        overall_timeout_seconds=2.5,
    )

    assert manager.command_calls == [
        {
            "file": str(source),
            "command": "Check Nat.add.",
            "line": 1,
            "character": 2,
            "overall_timeout_seconds": 2.5,
        },
        {
            "file": str(source),
            "command": "Search (_ + _).",
            "line": 1,
            "character": 2,
            "overall_timeout_seconds": 2.5,
        },
    ]
    assert dumped["status"] == "complete"
    assert dumped["file"] == "Demo.v"
    assert dumped["location"] == "1:2"
    assert [result["command"] for result in dumped["results"]] == [
        "Check Nat.add.",
        "Search (_ + _).",
    ]


def test_timeout_tool_descriptions_distinguish_soft_and_host_timeouts() -> None:
    compile_description = _field_description(
        rocq_lsp_compile_file, "overall_timeout_seconds"
    )
    command_description = _field_description(
        rocq_lsp_compile_file, "command_timeout_seconds"
    )
    run_command_description = _field_description(
        rocq_lsp_run_command, "overall_timeout_seconds"
    )

    assert "soft coq-lsp request deadline" in compile_description.lower()
    assert "MCP host cancels" in compile_description
    assert "partial diagnostics" in compile_description
    assert "per Rocq sentence watchdog" in command_description
    assert "slow sentence" in command_description
    assert "soft coq-lsp request deadline" in run_command_description.lower()
    assert "MCP host cancels" in run_command_description


def _field_description(function: Any, parameter: str) -> str:
    annotation = get_type_hints(function, include_extras=True)[parameter]
    for item in get_args(annotation):
        if isinstance(item, FieldInfo):
            return item.description or ""
    raise AssertionError(f"{parameter} has no FieldInfo")
