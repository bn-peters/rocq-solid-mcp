from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from rocq_lsp_mcp.models import CompileResult, Diagnostic
from rocq_lsp_mcp.server import rocq_lsp_compile_file


class FakeManager:
    def __init__(self, result: CompileResult) -> None:
        self.result = result
        self.compile_kwargs: dict[str, Any] | None = None

    async def compile_file(self, file: str, **kwargs: Any) -> CompileResult:
        self.compile_kwargs = {"file": file, **kwargs}
        return self.result


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
        "long_running_seconds": 3.0,
        "restart": False,
    }
    assert [item["message"] for item in dumped["diagnostics"]] == [
        "bad",
        "careful",
    ]
