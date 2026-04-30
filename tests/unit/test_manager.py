from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from rocq_lsp_mcp.config import (
    MAX_OVERALL_TIMEOUT_SECONDS,
    RocqLspConfig,
    TimeoutPolicy,
)
from rocq_lsp_mcp.manager import RocqLspManager
from rocq_lsp_mcp.models import RenderOptions
from rocq_lsp_mcp.notifications import NotificationStore
from rocq_lsp_mcp.rocq_client import (
    GetDocumentResult,
    RocqLspHardTimeout,
    RocqLspResponseError,
    language_id_for_path,
)
from rocq_lsp_mcp.session import RocqLspSession


class FakeClient:
    def __init__(
        self,
        notifications: NotificationStore,
        diagnostics: list[Any] | None = None,
        document: dict[str, Any] | None = None,
        goals_response: dict[str, Any] | Exception | None = None,
        file_progress: dict[str, Any] | None = None,
        request_timed_out: bool = False,
        command_response: dict[str, Any] | Exception | None = None,
        timeout_kind: str = "request",
        timed_out_range: dict[str, Any] | None = None,
    ) -> None:
        self.notifications = notifications
        self.diagnostics = diagnostics or []
        self.document = document
        self.goals_response = goals_response
        self.file_progress = file_progress
        self.request_timed_out = request_timed_out
        self.command_response = command_response
        self.timeout_kind = timeout_kind
        self.timed_out_range = timed_out_range
        self.opened: list[tuple[Path, int]] = []
        self.changed: list[tuple[Path, int]] = []
        self.workspace_updates = 0
        self.goal_requests: list[dict[str, Any]] = []
        self.get_document_requests: list[dict[str, Any]] = []
        self.command_requests: list[dict[str, Any]] = []
        self.events: list[str] = []
        self.killed = False
        self.closed = False
        self.pending_cleared = False
        self.hard_timeout = False

    async def notify_workspace_update(self) -> None:
        self.workspace_updates += 1
        self.events.append("workspace_update")

    async def notify_document_opened(
        self, file_path: Path, content: str, version: int
    ) -> None:
        self.opened.append((file_path, version))
        self.events.append(f"opened:{version}")

    async def notify_document_changed(
        self, file_path: Path, content: str, version: int
    ) -> None:
        self.changed.append((file_path, version))
        self.events.append(f"changed:{version}")

    async def request_get_document(
        self,
        *,
        uri: str,
        version: int,
        timeout: float,
        hard_timeout_grace: float,
        command_timeout: float | None = None,
        goals: str | None = None,
    ) -> GetDocumentResult:
        self.events.append(f"get_document:{version}")
        self.get_document_requests.append(
            {
                "uri": uri,
                "version": version,
                "timeout": timeout,
                "hard_timeout_grace": hard_timeout_grace,
                "command_timeout": command_timeout,
                "goals": goals,
            }
        )
        if self.file_progress is not None:
            self.notifications.handle("$/coq/fileProgress", self.file_progress)
        self.notifications.handle(
            "textDocument/publishDiagnostics",
            {"uri": uri, "version": version, "diagnostics": self.diagnostics},
        )
        if self.hard_timeout:
            raise RocqLspHardTimeout("stuck")
        return GetDocumentResult(
            document=(
                self.document
                if self.document is not None
                else {
                    "completed": {
                        "status": "Yes",
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 0},
                        },
                    }
                }
            ),
            timed_out=self.request_timed_out,
            timeout_kind=self.timeout_kind if self.request_timed_out else None,
            timed_out_range=self.timed_out_range,
        )

    async def request_goals(
        self,
        *,
        uri: str,
        version: int,
        position: dict[str, int],
        timeout: float | None = None,
        pp_format: str = "Str",
        compact: bool = True,
        mode: str = "Prev",
    ) -> dict[str, Any]:
        self.goal_requests.append(
            {
                "uri": uri,
                "version": version,
                "position": position,
                "timeout": timeout,
                "pp_format": pp_format,
                "compact": compact,
                "mode": mode,
            }
        )
        if isinstance(self.goals_response, Exception):
            raise self.goals_response
        return self.goals_response or {}

    async def request_run_at_point(
        self,
        *,
        uri: str,
        version: int,
        position: dict[str, int],
        command: str,
        timeout: float,
        hard_timeout_grace: float,
    ) -> tuple[dict[str, Any], bool]:
        self.command_requests.append(
            {
                "uri": uri,
                "version": version,
                "position": position,
                "command": command,
                "timeout": timeout,
                "hard_timeout_grace": hard_timeout_grace,
            }
        )
        if self.hard_timeout:
            raise RocqLspHardTimeout("stuck")
        if isinstance(self.command_response, Exception):
            raise self.command_response
        return self.command_response or {"feedback": []}, False

    def clear_pending_requests(self) -> None:
        self.pending_cleared = True

    async def kill_server(self) -> None:
        self.killed = True

    async def close(self) -> None:
        self.closed = True


class SpyNotificationStore(NotificationStore):
    def __init__(self) -> None:
        super().__init__()
        self.cleared: list[tuple[str, int]] = []

    def clear(self, uri: str, version: int) -> None:
        self.cleared.append((uri, version))
        super().clear(uri, version)


class FakeSession(RocqLspSession):
    def __init__(
        self,
        *,
        workspace: Path,
        config: RocqLspConfig,
        client: FakeClient,
        notifications: SpyNotificationStore,
    ) -> None:
        super().__init__(workspace=workspace, config=config)
        self._client = client
        self._notifications = notifications


class FakeManager(RocqLspManager):
    def __init__(
        self,
        client: FakeClient,
        notifications: SpyNotificationStore,
        workspace: Path | None,
        idle_ttl_seconds: float = 1800,
        command_seconds: float | None = None,
    ) -> None:
        config = RocqLspConfig(
            binary="coq-lsp",
            timeout_policy=TimeoutPolicy(
                request_seconds=1,
                cancel_grace_seconds=0.01,
                command_seconds=command_seconds,
            ),
            max_errors=1000000,
            workspace=workspace,
            idle_ttl_seconds=idle_ttl_seconds,
        )
        super().__init__(config=config)
        self.client = client
        self.notifications = notifications

    async def _create_session(self, workspace: Path) -> RocqLspSession:
        return FakeSession(
            workspace=workspace,
            config=self.config,
            client=self.client,
            notifications=self.notifications,
        )


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


def file_progress(
    uri: str,
    version: int,
    *,
    start_line: int,
    start_character: int,
    end_line: int,
    end_character: int,
) -> dict[str, Any]:
    return {
        "textDocument": {"uri": uri, "version": version},
        "processing": [
            {
                "range": {
                    "start": {"line": start_line, "character": start_character},
                    "end": {"line": end_line, "character": end_character},
                },
                "kind": 1,
            }
        ],
    }


@pytest.mark.asyncio
async def test_compile_file_opens_then_changes(tmp_path: Path) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications, [diagnostic("bad", 1), diagnostic("warn", 2)])
    manager = FakeManager(client, notifications, tmp_path)

    first = await manager.compile_file(str(source))
    second = await manager.compile_file(str(source))

    assert first.status == "complete"
    assert first.error_count == 1
    assert first.warning_count == 1
    assert first.version == 1
    assert second.version == 2
    assert client.opened == [(source.resolve(), 1)]
    assert client.changed == [(source.resolve(), 2)]
    assert notifications.cleared == [(source.as_uri(), 1), (source.as_uri(), 2)]


@pytest.mark.asyncio
async def test_compile_file_restart_notifies_workspace_before_change(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(client, notifications, tmp_path)

    first = await manager.compile_file(str(source))
    second = await manager.compile_file(str(source), restart=True)

    assert first.status == "complete"
    assert second.status == "complete"
    assert second.restarted is False
    assert client.workspace_updates == 1
    assert client.opened == [(source.resolve(), 1)]
    assert client.changed == [(source.resolve(), 2)]
    assert client.events == [
        "opened:1",
        "get_document:1",
        "workspace_update",
        "changed:2",
        "get_document:2",
    ]


@pytest.mark.asyncio
async def test_compile_file_keeps_only_first_error_per_proof_name_and_global_errors(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(
        notifications,
        [
            diagnostic("first foo", 1, proof_name="foo", line=0),
            diagnostic("second foo", 1, proof_name="foo", line=1),
            diagnostic("foo warning", 2, proof_name="foo", line=2),
            diagnostic("first bar", 1, proof_name="bar", line=3),
            diagnostic("global first", 1, line=4),
            diagnostic("global second", 1, line=5),
        ],
    )
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.compile_file(str(source))

    assert result.error_count == 5
    assert result.warning_count == 1
    assert [diagnostic.message for diagnostic in result.diagnostics] == [
        "first foo",
        "second foo",
        "foo warning",
        "first bar",
        "global first",
        "global second",
    ]
    assert [error.message for error in result.errors] == [
        "first foo",
        "second foo",
        "first bar",
        "global first",
        "global second",
    ]
    assert [diagnostic["message"] for diagnostic in result.render()["diagnostics"]] == [
        "first foo",
        "foo warning",
        "first bar",
        "global first",
    ]


@pytest.mark.asyncio
async def test_compile_file_accepts_requested_message_limit(tmp_path: Path) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(
        notifications,
        [
            *[
                diagnostic(
                    f"bad {index}",
                    1,
                    proof_name=f"proof_{index}",
                    line=index,
                )
                for index in range(3)
            ],
            *[diagnostic(f"warn {index}", 2, line=index + 3) for index in range(3)],
            *[diagnostic(f"note {index}", 3, line=index + 6) for index in range(3)],
        ],
    )
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.compile_file(str(source))

    assert [diagnostic.message for diagnostic in result.diagnostics] == [
        "bad 0",
        "bad 1",
        "bad 2",
        "warn 0",
        "warn 1",
        "warn 2",
        "note 0",
        "note 1",
        "note 2",
    ]
    assert result.error_count == 3
    assert result.warning_count == 3
    assert result.info_count == 3
    assert result.render(RenderOptions(message_limit=2))["omitted_counts"] == {
        "errors": 1,
        "warnings": 1,
        "infos": 1,
    }


@pytest.mark.asyncio
async def test_compile_file_forwards_overall_timeout_override(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(client, notifications, tmp_path)

    await manager.compile_file(str(source), overall_timeout_seconds=2.5)
    await manager.compile_file(str(source), overall_timeout_seconds=9999)

    assert client.get_document_requests[0]["timeout"] == 2.5
    assert (
        client.get_document_requests[1]["timeout"]
        == MAX_OVERALL_TIMEOUT_SECONDS
    )


@pytest.mark.asyncio
async def test_compile_file_enriches_incomplete_proof_save_error(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Goal Foo.\nProof.\nQed.\n", encoding="utf-8")
    range_ = {
        "start": {"line": 2, "character": 0},
        "end": {"line": 2, "character": 4},
    }
    notifications = SpyNotificationStore()
    client = FakeClient(
        notifications,
        [
            {
                "range": range_,
                "message": " (in proof foo): Attempt to save an incomplete proof\n"
                "(there are remaining open goals).",
                "severity": 1,
                "data": {"sentenceRange": range_, "proofName": "foo"},
            }
        ],
        document={
            "completed": {
                "status": "Yes",
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 2, "character": 4},
                },
            },
            "spans": [
                {
                    "range": range_,
                    "goals": {
                        "program": [],
                        "messages": [],
                        "error": " (in proof foo): Attempt to save an incomplete proof\n"
                        "(there are remaining open goals).",
                    },
                }
            ],
        },
        goals_response={
            "goals": {
                "goals": [
                    {
                        "info": {"name": None},
                        "hyps": [],
                        "ty": "Foo",
                    }
                ],
                "stack": [],
                "bullet": None,
                "shelf": [],
                "given_up": [],
            },
            "program": [["noisy"]],
        },
    )
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.compile_file(str(source), overall_timeout_seconds=2.5)

    assert result.error_count == 1
    assert client.goal_requests == [
        {
            "uri": source.as_uri(),
            "version": 1,
            "position": {"line": 2, "character": 0},
            "timeout": 2.5,
            "pp_format": "Str",
            "compact": True,
            "mode": "Prev",
        }
    ]
    proof_state = result.diagnostics[0].proof_state
    assert proof_state == {"goals": [{"conclusion": "Foo"}]}


@pytest.mark.asyncio
async def test_compile_file_skips_goal_request_for_regular_proof_error(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Goal 0 = 0. Proof. fail. Qed.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(
        notifications,
        [diagnostic("Tactic failure.", 1, proof_name="foo", line=0)],
    )
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.compile_file(str(source))

    assert result.error_count == 1
    assert client.goal_requests == []


@pytest.mark.asyncio
async def test_compile_file_adds_timeout_context_from_file_progress(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Slow.v"
    source.write_text(
        "Goal True.\nProof.\n  sleep 5.\n  easy.\nQed.\n",
        encoding="utf-8",
    )
    notifications = SpyNotificationStore()
    client = FakeClient(
        notifications,
        document={},
        file_progress=file_progress(
            source.as_uri(),
            1,
            start_line=2,
            start_character=2,
            end_line=4,
            end_character=4,
        ),
        request_timed_out=True,
    )
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.compile_file(str(source), overall_timeout_seconds=2.5)

    assert result.status == "timeout"
    assert result.timed_out is True
    assert result.render()["timeout_context"] == {
        "operation": "coq/getDocument",
        "timeout_kind": "request",
        "request_timeout_seconds": 2.5,
        "cancel_grace_seconds": 0.01,
        "checked_until": "3:3",
        "latest_processing_range": "3:3 to 5:5",
        "next_unchecked": {"location": "3:3", "line": "sleep 5."},
    }
    assert client.get_document_requests[0]["timeout"] == 2.5


@pytest.mark.asyncio
async def test_compile_file_prefers_completed_position_for_timeout_context(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Slow.v"
    source.write_text("Check nat.\nCheck bool.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(
        notifications,
        document={
            "completed": {
                "status": "Yes",
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 10},
                },
            }
        },
        file_progress=file_progress(
            source.as_uri(),
            1,
            start_line=0,
            start_character=0,
            end_line=1,
            end_character=11,
        ),
        request_timed_out=True,
    )
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.compile_file(str(source))

    assert result.render()["timeout_context"] == {
        "operation": "coq/getDocument",
        "timeout_kind": "request",
        "request_timeout_seconds": 1.0,
        "cancel_grace_seconds": 0.01,
        "checked_until": "1:11",
        "latest_processing_range": "1:1 to 2:12",
        "next_unchecked": {"location": "2:1", "line": "Check bool."},
    }


@pytest.mark.asyncio
async def test_run_command_forwards_position_and_returns_feedback(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("From Stdlib Require Import Arith.PeanoNat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(
        notifications,
        command_response={
            "feedback": [[3, "Nat.add_0_r: forall n : nat, n + 0 = n"]],
        },
    )
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.run_command(str(source), "Search (_ + 0 = _).", 1, 2)

    assert result.status == "complete"
    assert result.version == 1
    assert result.location == "1:2"
    assert result.output[0].render() == {
        "level": "info",
        "text": "Nat.add_0_r: forall n : nat, n + 0 = n",
    }
    assert client.opened == [(source.resolve(), 1)]
    assert client.command_requests == [
        {
            "uri": source.as_uri(),
            "version": 1,
            "position": {"line": 0, "character": 1},
            "command": "Search (_ + 0 = _).",
            "timeout": 1,
            "hard_timeout_grace": 0.01,
        }
    ]


@pytest.mark.asyncio
async def test_run_command_forwards_overall_timeout_override(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(client, notifications, tmp_path)

    await manager.run_command(
        str(source),
        "Search nat.",
        1,
        1,
        overall_timeout_seconds=2.5,
    )
    await manager.run_command(
        str(source),
        "Search nat.",
        1,
        1,
        overall_timeout_seconds=9999,
    )

    assert client.command_requests[0]["timeout"] == 2.5
    assert client.command_requests[1]["timeout"] == MAX_OVERALL_TIMEOUT_SECONDS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "line", "character", "message"),
    [
        ("", 1, 1, "command must be a non-empty Rocq command ending in '.'"),
        ("Check nat", 1, 1, "command must be a non-empty Rocq command ending in '.'"),
        ("Check nat.", 0, 1, "line and character must be positive 1-based integers"),
        ("Check nat.", 1, 0, "line and character must be positive 1-based integers"),
    ],
)
async def test_run_command_validates_before_lsp_request(
    tmp_path: Path,
    command: str,
    line: int,
    character: int,
    message: str,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.run_command(str(source), command, line, character)

    assert result.status == "failed"
    assert result.message == message
    assert client.opened == []
    assert client.changed == []
    assert client.command_requests == []


@pytest.mark.asyncio
async def test_run_command_response_error_includes_feedback(tmp_path: Path) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(
        notifications,
        command_response=RocqLspResponseError(
            {
                "message": "Coq: command failed",
                "data": {"feedback": [[3, "partial output"]]},
            }
        ),
    )
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.run_command(str(source), "Check missing.", 1, 1)

    assert result.status == "failed"
    assert result.message == "Coq: command failed"
    assert result.output[0].render() == {"level": "info", "text": "partial output"}


@pytest.mark.asyncio
async def test_run_command_hard_timeout_resets_session(tmp_path: Path) -> None:
    source = tmp_path / "Slow.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    client.hard_timeout = True
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.run_command(str(source), "Search nat.", 1, 1)

    assert result.status == "timeout"
    assert result.timed_out is True
    assert result.restarted is True
    assert client.pending_cleared is True
    assert client.killed is True
    assert tmp_path not in manager._sessions


@pytest.mark.asyncio
async def test_compile_file_adds_command_timeout_context(tmp_path: Path) -> None:
    source = tmp_path / "Slow.v"
    source.write_text(
        "Goal True.\nProof.\n  sleep 5.\n  easy.\nQed.\n",
        encoding="utf-8",
    )
    timed_out_range = {
        "start": {"line": 2, "character": 2},
        "end": {"line": 2, "character": 10},
    }
    notifications = SpyNotificationStore()
    client = FakeClient(
        notifications,
        document={},
        file_progress=file_progress(
            source.as_uri(),
            1,
            start_line=2,
            start_character=2,
            end_line=4,
            end_character=4,
        ),
        request_timed_out=True,
        timeout_kind="command",
        timed_out_range=timed_out_range,
    )
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.compile_file(str(source), command_timeout_seconds=2)

    assert result.status == "timeout"
    assert result.render()["timeout_context"] == {
        "operation": "coq/getDocument",
        "timeout_kind": "command",
        "request_timeout_seconds": 1.0,
        "cancel_grace_seconds": 0.01,
        "checked_until": "3:3",
        "latest_processing_range": "3:3 to 5:5",
        "next_unchecked": {"location": "3:3", "line": "sleep 5."},
        "command_timeout_seconds": 2,
        "timed_out_range": "3:3 to 3:11",
        "timed_out_command": {"location": "3:3", "line": "sleep 5."},
    }
    assert client.get_document_requests[0]["command_timeout"] == 2


@pytest.mark.asyncio
async def test_hard_timeout_resets_session(tmp_path: Path) -> None:
    source = tmp_path / "Slow.v"
    source.write_text("Goal True.\nProof.\n  sleep 5.\nQed.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(
        notifications,
        [diagnostic("partial", 1)],
        file_progress=file_progress(
            source.as_uri(),
            1,
            start_line=2,
            start_character=2,
            end_line=3,
            end_character=4,
        ),
    )
    client.hard_timeout = True
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.compile_file(str(source))

    assert result.status == "timeout"
    assert result.timed_out is True
    assert result.restarted is True
    assert result.error_count == 1
    assert result.render()["timeout_context"] == {
        "operation": "coq/getDocument",
        "timeout_kind": "request",
        "request_timeout_seconds": 1.0,
        "cancel_grace_seconds": 0.01,
        "checked_until": "3:3",
        "latest_processing_range": "3:3 to 4:5",
        "next_unchecked": {"location": "3:3", "line": "sleep 5."},
    }
    assert notifications.cleared == [(source.as_uri(), 1)]
    assert client.pending_cleared is True
    assert client.killed is True
    assert tmp_path not in manager._sessions


@pytest.mark.asyncio
async def test_compile_file_uses_separate_sessions_for_project_roots(
    tmp_path: Path,
) -> None:
    project_a = tmp_path / "a"
    project_b = tmp_path / "b"
    project_a.mkdir()
    project_b.mkdir()
    (project_a / "_RocqProject").write_text("-Q . A\n", encoding="utf-8")
    (project_b / "_CoqProject").write_text("-Q . B\n", encoding="utf-8")
    source_a = project_a / "A.v"
    source_b = project_b / "B.v"
    source_a.write_text("Check nat.\n", encoding="utf-8")
    source_b.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(client, notifications, None)

    await manager.compile_file(str(source_a))
    await manager.compile_file(str(source_b))

    assert set(manager._sessions) == {project_a.resolve(), project_b.resolve()}
    assert client.opened == [(source_a.resolve(), 1), (source_b.resolve(), 1)]


@pytest.mark.asyncio
async def test_idle_cleanup_stops_stale_unlocked_sessions(tmp_path: Path) -> None:
    notifications = SpyNotificationStore()
    manager = FakeManager(
        FakeClient(notifications),
        notifications,
        None,
        idle_ttl_seconds=1,
    )
    stale_workspace = tmp_path / "stale"
    fresh_workspace = tmp_path / "fresh"
    stale_workspace.mkdir()
    fresh_workspace.mkdir()
    stale_client = FakeClient(notifications)
    fresh_client = FakeClient(notifications)
    stale_session = FakeSession(
        workspace=stale_workspace,
        config=manager.config,
        client=stale_client,
        notifications=notifications,
    )
    fresh_session = FakeSession(
        workspace=fresh_workspace,
        config=manager.config,
        client=fresh_client,
        notifications=notifications,
    )
    stale_session.last_used_at = time.monotonic() - 2
    fresh_session.last_used_at = time.monotonic()
    manager._sessions[stale_workspace] = stale_session
    manager._sessions[fresh_workspace] = fresh_session

    await manager._cleanup_idle_sessions()

    assert stale_workspace not in manager._sessions
    assert fresh_workspace in manager._sessions
    assert stale_client.closed is True
    assert fresh_client.closed is False


@pytest.mark.asyncio
async def test_idle_cleanup_keeps_locked_sessions(tmp_path: Path) -> None:
    notifications = SpyNotificationStore()
    manager = FakeManager(
        FakeClient(notifications),
        notifications,
        None,
        idle_ttl_seconds=1,
    )
    workspace = tmp_path / "locked"
    workspace.mkdir()
    client = FakeClient(notifications)
    session = FakeSession(
        workspace=workspace,
        config=manager.config,
        client=client,
        notifications=notifications,
    )
    session.last_used_at = time.monotonic() - 2
    manager._sessions[workspace] = session
    await session.lock.acquire()

    try:
        await manager._cleanup_idle_sessions()
    finally:
        session.lock.release()

    assert manager._sessions[workspace] is session
    assert client.closed is False


@pytest.mark.asyncio
async def test_checkout_retries_when_waiting_on_reset_session(tmp_path: Path) -> None:
    notifications = SpyNotificationStore()
    manager = FakeManager(FakeClient(notifications), notifications, tmp_path)
    original = await manager._checkout_session(tmp_path)

    waiting = asyncio.create_task(manager._checkout_session(tmp_path))
    while original.active_requests < 2:
        await asyncio.sleep(0)

    await manager._reset_session(tmp_path)
    original.lock.release()
    await manager._release_session(original)
    replacement = await asyncio.wait_for(waiting, timeout=1)

    try:
        assert replacement is not original
        assert manager._sessions[tmp_path] is replacement
    finally:
        replacement.lock.release()
        await manager._release_session(replacement)


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
            timeout_policy=TimeoutPolicy(request_seconds=1, cancel_grace_seconds=1),
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
