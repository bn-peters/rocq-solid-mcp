from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rocq_lsp_mcp import cli
from rocq_lsp_mcp.models import CheckResult, Diagnostic


class FakeManager:
    instances: list["FakeManager"] = []

    def __init__(self) -> None:
        self.check_kwargs: dict[str, Any] | None = None
        self.closed = False
        self.instances.append(self)

    async def check_file(self, file: str, **kwargs: Any) -> CheckResult:
        self.check_kwargs = {"file": file, **kwargs}
        diagnostics = [
            Diagnostic.from_lsp(
                {"range": {}, "message": f"bad {index}", "severity": 1}
            ).model_copy(update={"proof_name": f"proof_{index}"})
            for index in range(12)
        ]
        return CheckResult.from_diagnostics(
            status="complete",
            file=file,
            uri=Path(file).resolve().as_uri(),
            workspace=str(Path(file).resolve().parent),
            version=1,
            completed=None,
            diagnostics=diagnostics,
        )

    async def close(self) -> None:
        self.closed = True


def test_check_prints_formatted_json_without_cli_limits(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    FakeManager.instances.clear()
    monkeypatch.setattr(cli, "RocqLspManager", FakeManager)

    exit_code = cli.main(["check", str(source)])

    output = capsys.readouterr().out
    dumped = json.loads(output)
    manager = FakeManager.instances[0]
    assert exit_code == 0
    assert output.startswith("{\n  ")
    assert manager.check_kwargs == {
        "file": str(source),
        "command_timeout_seconds": 0,
        "command_timeout_after_error_seconds": 0,
        "overall_timeout_seconds": 0,
        "long_running_seconds": 0,
    }
    assert manager.closed is True
    assert dumped["status"] == "complete"
    assert dumped["file"] == "Demo.v"
    assert len(dumped["diagnostics"]) == 12
    assert "message_limit" not in dumped
    assert "omitted_counts" not in dumped


def test_no_arguments_runs_mcp_server(monkeypatch) -> None:
    called = False

    def fake_run_mcp_server() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli, "run_mcp_server", fake_run_mcp_server)

    assert cli.main([]) is None
    assert called is True
