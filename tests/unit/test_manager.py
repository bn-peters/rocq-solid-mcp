from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from rocq_lsp_mcp.config import RocqLspConfig
from rocq_lsp_mcp.manager import RocqLspManager, RocqLspSession
from rocq_lsp_mcp.rocq_client import RocqLspHardTimeout, language_id_for_path


class FakeClient:
    def __init__(self, diagnostics: list[Any] | None = None) -> None:
        self.diagnostics = diagnostics or []
        self.opened: list[tuple[Path, int]] = []
        self.changed: list[tuple[Path, int]] = []
        self.cleared: list[tuple[str, int]] = []
        self.killed = False
        self.pending_cleared = False
        self.hard_timeout = False

    def clear_diagnostics(self, uri: str, version: int) -> None:
        self.cleared.append((uri, version))

    async def notify_document_opened(
        self, file_path: Path, content: str, version: int
    ) -> None:
        self.opened.append((file_path, version))

    async def notify_document_changed(
        self, file_path: Path, content: str, version: int
    ) -> None:
        self.changed.append((file_path, version))

    async def request_get_document(
        self,
        *,
        uri: str,
        version: int,
        timeout: float,
        hard_timeout_grace: float,
        goals: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if self.hard_timeout:
            raise RocqLspHardTimeout("stuck")
        return (
            {
                "completed": {
                    "status": "Yes",
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 0},
                    },
                }
            },
            False,
        )

    async def wait_for_diagnostics(self, uri: str, version: int) -> list[Any]:
        return self.diagnostics

    def clear_pending_requests(self) -> None:
        self.pending_cleared = True

    async def kill_server(self) -> None:
        self.killed = True


class FakeManager(RocqLspManager):
    def __init__(self, client: FakeClient, workspace: Path) -> None:
        config = RocqLspConfig(
            binary="coq-lsp",
            timeout=1,
            hard_timeout_grace=0.01,
            max_errors=1000000,
            workspace=workspace,
        )
        super().__init__(config=config)
        self.client = client

    async def _get_or_create_session(self, workspace: Path) -> RocqLspSession:
        if session := self._sessions.get(workspace):
            return session
        session = RocqLspSession(
            workspace=workspace,
            client=self.client,  # type: ignore[arg-type]
        )
        self._sessions[workspace] = session
        return session


def diagnostic(
    message: str,
    severity: int,
    *,
    proof_name: str | None = None,
    line: int = 0,
) -> dict[str, Any]:
    range_ = {
        "start": {"line": line, "character": 0},
        "end": {"line": line, "character": 1},
    }
    result: dict[str, Any] = {
        "range": {
            "start": {"line": line, "character": 0},
            "end": {"line": line, "character": 1},
        },
        "message": message,
        "severity": severity,
    }
    if proof_name is not None:
        result["data"] = {"sentenceRange": range_, "proofName": proof_name}
    return result


@pytest.mark.asyncio
async def test_compile_file_opens_then_changes(tmp_path: Path) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    client = FakeClient([diagnostic("bad", 1), diagnostic("warn", 2)])
    manager = FakeManager(client, tmp_path)

    first = await manager.compile_file(str(source))
    second = await manager.compile_file(str(source))

    assert first.status == "complete"
    assert first.error_count == 1
    assert first.warning_count == 1
    assert first.version == 1
    assert second.version == 2
    assert client.opened == [(source.resolve(), 1)]
    assert client.changed == [(source.resolve(), 2)]


@pytest.mark.asyncio
async def test_compile_file_keeps_only_first_error_per_proof_name(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    client = FakeClient(
        [
            diagnostic("first foo", 1, proof_name="foo", line=0),
            diagnostic("second foo", 1, proof_name="foo", line=1),
            diagnostic("foo warning", 2, proof_name="foo", line=2),
            diagnostic("first bar", 1, proof_name="bar", line=3),
            diagnostic("global first", 1, line=4),
            diagnostic("global second", 1, line=5),
        ]
    )
    manager = FakeManager(client, tmp_path)

    result = await manager.compile_file(str(source))

    assert result.error_count == 4
    assert result.warning_count == 1
    assert [diagnostic.message for diagnostic in result.diagnostics] == [
        "first foo",
        "foo warning",
        "first bar",
        "global first",
        "global second",
    ]
    assert [error.message for error in result.errors] == [
        "first foo",
        "first bar",
        "global first",
        "global second",
    ]


@pytest.mark.asyncio
async def test_hard_timeout_resets_session(tmp_path: Path) -> None:
    source = tmp_path / "Slow.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    client = FakeClient([diagnostic("partial", 1)])
    client.hard_timeout = True
    manager = FakeManager(client, tmp_path)

    result = await manager.compile_file(str(source))

    assert result.status == "timeout"
    assert result.timed_out is True
    assert result.restarted is True
    assert result.error_count == 1
    assert client.pending_cleared is True
    assert client.killed is True
    assert tmp_path not in manager._sessions


def test_workspace_inference_prefers_project_file(tmp_path: Path) -> None:
    root = tmp_path / "project"
    nested = root / "theories"
    nested.mkdir(parents=True)
    (root / "_RocqProject").write_text("-Q theories Demo\n", encoding="utf-8")
    source = nested / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    manager = RocqLspManager(
        RocqLspConfig(
            binary="coq-lsp",
            timeout=1,
            hard_timeout_grace=1,
            max_errors=1000000,
            workspace=None,
        )
    )

    assert manager._infer_workspace(source) == root.resolve()


def test_language_id_for_path() -> None:
    assert language_id_for_path(Path("A.v")) == "rocq"
    assert language_id_for_path(Path("A.mv")) == "markdown"
    assert language_id_for_path(Path("A.lv")) == "latex"
    assert language_id_for_path(Path("A.v.tex")) == "latex"
