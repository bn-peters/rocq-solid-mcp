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
from rocq_lsp_mcp.manager import (
    DuneBuildResult,
    RocqLspManager,
    _dune_reported_build_actions,
)
from rocq_lsp_mcp.models import RenderOptions
from rocq_lsp_mcp.notifications import NotificationStore
from rocq_lsp_mcp.rocq_client import (
    GetDocumentResult,
    RocqLspHardTimeout,
    RocqLspResponseError,
    RocqLspUnavailableError,
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
        file_perf_data: dict[str, Any] | None = None,
        request_timed_out: bool = False,
        run_at_response: dict[str, Any] | Exception | None = None,
        timeout_kind: str = "request",
        timed_out_range: dict[str, Any] | None = None,
    ) -> None:
        self.notifications = notifications
        self.diagnostics = diagnostics or []
        self.document = document
        self.goals_response = goals_response
        self.file_progress = file_progress
        self.file_perf_data = file_perf_data
        self.request_timed_out = request_timed_out
        self.run_at_response = run_at_response
        self.timeout_kind = timeout_kind
        self.timed_out_range = timed_out_range
        self.opened: list[tuple[Path, int]] = []
        self.changed: list[tuple[Path, int]] = []
        self.goal_requests: list[dict[str, Any]] = []
        self.get_document_requests: list[dict[str, Any]] = []
        self.run_at_requests: list[dict[str, Any]] = []
        self.events: list[str] = []
        self.killed = False
        self.closed = False
        self.pending_cleared = False
        self.hard_timeout = False

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
        timeout: float | None,
        hard_timeout_grace: float,
        command_timeout: float | None = None,
        command_timeout_after_error: float | None = None,
        goals: str | None = None,
        content: str | None = None,
    ) -> GetDocumentResult:
        self.events.append(f"get_document:{version}")
        self.get_document_requests.append(
            {
                "uri": uri,
                "version": version,
                "timeout": timeout,
                "hard_timeout_grace": hard_timeout_grace,
                "command_timeout": command_timeout,
                "command_timeout_after_error": command_timeout_after_error,
                "goals": goals,
                "content": content,
            }
        )
        if self.file_progress is not None:
            self.notifications.handle("$/coq/fileProgress", self.file_progress)
        if self.file_perf_data is not None:
            self.notifications.handle("$/coq/filePerfData", self.file_perf_data)
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
        commands: str,
        timeout: float | None,
        hard_timeout_grace: float,
    ) -> tuple[dict[str, Any], bool]:
        self.run_at_requests.append(
            {
                "uri": uri,
                "version": version,
                "position": position,
                "commands": commands,
                "timeout": timeout,
                "hard_timeout_grace": hard_timeout_grace,
            }
        )
        if self.hard_timeout:
            raise RocqLspHardTimeout("stuck")
        if isinstance(self.run_at_response, Exception):
            raise self.run_at_response
        return self.run_at_response or {"feedback": []}, False

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
        command_seconds_after_error: float | None = None,
        dune_build_result: DuneBuildResult | None = None,
    ) -> None:
        config = RocqLspConfig(
            binary="coq-lsp",
            timeout_policy=TimeoutPolicy(
                request_seconds=1,
                cancel_grace_seconds=0.01,
                command_seconds=command_seconds,
                command_seconds_after_error=command_seconds_after_error,
            ),
            max_errors=1000000,
            workspace=workspace,
            idle_ttl_seconds=idle_ttl_seconds,
        )
        super().__init__(config=config)
        self.client = client
        self.notifications = notifications
        self.dune_build_result = dune_build_result
        self.dune_build_paths: list[Path] = []

    async def _create_session(self, workspace: Path) -> RocqLspSession:
        return FakeSession(
            workspace=workspace,
            config=self.config,
            client=self.client,
            notifications=self.notifications,
        )

    async def _maybe_build_dune_dependencies(self, path: Path) -> DuneBuildResult:
        self.dune_build_paths.append(path)
        return self.dune_build_result or DuneBuildResult()


class FakeDuneCommandManager(FakeManager):
    def __init__(
        self,
        client: FakeClient,
        notifications: SpyNotificationStore,
        workspace: Path | None,
        results: list[tuple[int, str, str] | None],
    ) -> None:
        super().__init__(client, notifications, workspace)
        self.results = results
        self.dune_commands: list[tuple[Path, tuple[str, ...]]] = []

    async def _maybe_build_dune_dependencies(self, path: Path) -> DuneBuildResult:
        return await RocqLspManager._maybe_build_dune_dependencies(self, path)

    async def _run_dune_command_unlocked(
        self,
        cwd: Path,
        command: tuple[str, ...],
    ) -> tuple[int, str, str] | None:
        self.dune_commands.append((cwd, command))
        return self.results.pop(0)


class SlowDuneCommandManager(RocqLspManager):
    def __init__(self, workspace: Path) -> None:
        config = RocqLspConfig(
            binary="coq-lsp",
            timeout_policy=TimeoutPolicy(
                request_seconds=1,
                cancel_grace_seconds=0.01,
            ),
            max_errors=1000000,
            workspace=workspace,
        )
        super().__init__(config=config)
        self.active_commands = 0
        self.max_active_commands = 0

    async def _run_dune_command_unlocked(
        self,
        cwd: Path,
        command: tuple[str, ...],
    ) -> tuple[int, str, str] | None:
        self.active_commands += 1
        self.max_active_commands = max(self.max_active_commands, self.active_commands)
        try:
            await asyncio.sleep(0.01)
            return (0, "", "")
        finally:
            self.active_commands -= 1


class UnavailableManager(RocqLspManager):
    def __init__(self, workspace: Path, message: str) -> None:
        config = RocqLspConfig(
            binary="coq-lsp",
            timeout_policy=TimeoutPolicy(
                request_seconds=1,
                cancel_grace_seconds=0.01,
            ),
            max_errors=1000000,
            workspace=workspace,
        )
        super().__init__(config=config)
        self.message = message

    async def _create_session(self, workspace: Path) -> RocqLspSession:
        raise RocqLspUnavailableError(self.message)


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


def file_perf_data(
    uri: str,
    version: int,
    *,
    line: int,
    start_character: int,
    end_character: int,
    seconds: float,
    cache_hit: bool = False,
) -> dict[str, Any]:
    return {
        "textDocument": {"uri": uri, "version": version},
        "summary": "",
        "timings": [
            {
                "range": {
                    "start": {"line": line, "character": start_character},
                    "end": {"line": line, "character": end_character},
                },
                "info": {
                    "time": seconds,
                    "memory": 0,
                    "cache_hit": cache_hit,
                    "time_hash": 0.0,
                },
            }
        ],
    }


@pytest.mark.asyncio
async def test_check_file_reports_unavailable_coq_lsp_startup(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    message = "Unable to start coq-lsp: required executable 'coq-lsp' was not found."
    manager = UnavailableManager(tmp_path, message)

    result = await manager.check_file(str(source))

    rendered = result.render()
    assert result.status == "failed"
    assert result.message == message
    assert result.workspace == str(tmp_path)
    assert rendered["status"] == "failed"
    assert rendered["file"] == "Demo.v"
    assert rendered["message"] == message


@pytest.mark.asyncio
async def test_run_at_reports_unavailable_coq_lsp_startup(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    message = "Unable to start coq-lsp: required executable 'coq-lsp' was not found."
    manager = UnavailableManager(tmp_path, message)

    result = await manager.run_at(str(source), "Check nat.", 1)

    rendered = result.render()
    assert result.status == "failed"
    assert result.message == message
    assert result.workspace == str(tmp_path)
    assert rendered["status"] == "failed"
    assert rendered["file"] == "Demo.v"
    assert rendered["commands"] == "Check nat."
    assert rendered["message"] == message


@pytest.mark.asyncio
async def test_check_file_opens_then_changes(tmp_path: Path) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications, [diagnostic("bad", 1), diagnostic("warn", 2)])
    manager = FakeManager(client, notifications, tmp_path)

    first = await manager.check_file(str(source))
    second = await manager.check_file(str(source))

    assert first.status == "complete"
    assert first.error_count == 1
    assert first.warning_count == 1
    assert first.version == 1
    assert second.version == 2
    assert client.opened == [(source.resolve(), 1)]
    assert client.changed == [(source.resolve(), 2)]
    assert notifications.cleared == [(source.as_uri(), 1), (source.as_uri(), 2)]


@pytest.mark.asyncio
async def test_check_file_clear_cache_restarts_process_before_open(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(client, notifications, tmp_path)

    first = await manager.check_file(str(source))
    second = await manager.check_file(str(source), clear_cache=True)

    assert first.status == "complete"
    assert second.status == "complete"
    assert second.restarted is True
    assert client.pending_cleared is True
    assert client.killed is True
    assert client.opened == [(source.resolve(), 1), (source.resolve(), 1)]
    assert client.changed == []
    assert client.events == [
        "opened:1",
        "get_document:1",
        "opened:1",
        "get_document:1",
    ]


@pytest.mark.asyncio
async def test_check_file_clear_cache_does_not_restart_new_session(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.check_file(str(source), clear_cache=True)

    assert result.status == "complete"
    assert result.restarted is False
    assert client.pending_cleared is False
    assert client.killed is False
    assert client.opened == [(source.resolve(), 1)]
    assert client.changed == []


@pytest.mark.asyncio
async def test_check_file_dune_build_restarts_existing_process_before_open(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(
        client,
        notifications,
        tmp_path,
        dune_build_result=DuneBuildResult(applied=True),
    )

    first = await manager.check_file(str(source))
    second = await manager.check_file(str(source))

    assert first.status == "complete"
    assert first.restarted is False
    assert second.status == "complete"
    assert second.restarted is True
    assert manager.dune_build_paths == [source.resolve(), source.resolve()]
    assert client.pending_cleared is True
    assert client.killed is True
    assert client.opened == [(source.resolve(), 1), (source.resolve(), 1)]
    assert client.changed == []
    assert client.events == [
        "opened:1",
        "get_document:1",
        "opened:1",
        "get_document:1",
    ]


@pytest.mark.asyncio
async def test_check_file_dune_build_does_not_restart_new_session(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(
        client,
        notifications,
        tmp_path,
        dune_build_result=DuneBuildResult(applied=True),
    )

    result = await manager.check_file(str(source))

    assert result.status == "complete"
    assert result.restarted is False
    assert client.pending_cleared is False
    assert client.killed is False
    assert client.opened == [(source.resolve(), 1)]
    assert client.changed == []


@pytest.mark.asyncio
async def test_check_file_clear_cache_and_dune_build_restart_once(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(
        client,
        notifications,
        tmp_path,
        dune_build_result=DuneBuildResult(applied=True),
    )

    await manager.check_file(str(source))
    result = await manager.check_file(str(source), clear_cache=True)

    assert result.status == "complete"
    assert result.restarted is True
    assert client.opened == [(source.resolve(), 1), (source.resolve(), 1)]
    assert client.changed == []


@pytest.mark.asyncio
async def test_check_file_dune_build_failure_does_not_open_document(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(
        client,
        notifications,
        tmp_path,
        dune_build_result=DuneBuildResult(message="dune failed"),
    )

    result = await manager.check_file(str(source))

    assert result.status == "failed"
    assert result.message == "dune failed"
    assert result.workspace == str(tmp_path)
    assert client.opened == []
    assert client.changed == []
    assert client.get_document_requests == []


@pytest.mark.asyncio
async def test_dune_commands_are_serialized(tmp_path: Path) -> None:
    manager = SlowDuneCommandManager(tmp_path)

    await asyncio.gather(
        manager._run_dune_command(tmp_path, ("dune", "describe", "workspace")),
        manager._run_dune_command(tmp_path, ("dune", "describe", "workspace")),
    )

    assert manager.max_active_commands == 1


@pytest.mark.asyncio
async def test_check_file_dune_build_restarts_only_when_build_output_exists(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeDuneCommandManager(
        client,
        notifications,
        tmp_path,
        results=[
            (0, "workspace", ""),
            (0, "-topfile Demo.v", ""),
            (0, "-topfile Demo.v", ""),
            (0, "-topfile Demo.v", ""),
            (0, "-topfile Demo.v", "        rocq theories/Base.{glob,vo}\n"),
        ],
    )

    first = await manager.check_file(str(source))
    second = await manager.check_file(str(source))
    third = await manager.check_file(str(source))

    assert first.status == "complete"
    assert first.restarted is False
    assert second.status == "complete"
    assert second.restarted is False
    assert third.status == "complete"
    assert third.restarted is True
    assert client.opened == [(source.resolve(), 1), (source.resolve(), 1)]
    assert client.changed == [(source.resolve(), 2)]
    assert manager.dune_commands == [
        (tmp_path, ("dune", "describe", "workspace")),
        (
            tmp_path,
            (
                "dune",
                "rocq",
                "top",
                "--no-build",
                "--toplevel",
                "echo",
                "Demo.v",
            ),
        ),
        (
            tmp_path,
            (
                "dune",
                "rocq",
                "top",
                "Demo.v",
                "--toplevel",
                "echo",
                "--display=short",
            ),
        ),
        (
            tmp_path,
            (
                "dune",
                "rocq",
                "top",
                "Demo.v",
                "--toplevel",
                "echo",
                "--display=short",
            ),
        ),
        (
            tmp_path,
            (
                "dune",
                "rocq",
                "top",
                "Demo.v",
                "--toplevel",
                "echo",
                "--display=short",
            ),
        ),
    ]


def test_dune_reported_build_actions_ignores_directory_navigation() -> None:
    noise = (
        "Entering directory '/home/user/project'\n"
        "Leaving directory '/home/user/project'\n"
    )
    assert _dune_reported_build_actions(noise) is False
    assert _dune_reported_build_actions("") is False
    assert _dune_reported_build_actions("   \n") is False
    assert (
        _dune_reported_build_actions(
            noise + "        rocq theories/Base.{glob,vo}\n"
        )
        is True
    )
    assert _dune_reported_build_actions("        rocq theories/Base.vo\n") is True


def test_dune_reported_build_actions_ignores_coqdep_theory_d() -> None:
    # Editing the target file makes dune regenerate the theory's coqdep graph
    # (``.theory.d``) without recompiling any dependency ``.vo``. That must not
    # count as a rebuild, else every edit-then-recheck restarts the session.
    noise = "Entering directory '/p'\nLeaving directory '/p'\n"
    only_coqdep = noise + "        rocq clients/.demo_clients.theory.d\n"
    assert _dune_reported_build_actions(only_coqdep) is False
    # A real dependency compile alongside the coqdep line still counts.
    with_vo = only_coqdep + "        rocq theories/Base.{glob,vo}\n"
    assert _dune_reported_build_actions(with_vo) is True


@pytest.mark.asyncio
async def test_check_file_dune_build_ignores_directory_navigation_noise(
    tmp_path: Path,
) -> None:
    # An up-to-date dependency build still prints "Entering directory"/"Leaving
    # directory" to stderr when dune runs from a theory subdirectory. That noise
    # must not be read as a rebuild, otherwise every recheck restarts the
    # session and discards the incremental cache.
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    noise = "Entering directory '/root'\nLeaving directory '/root'\n"
    manager = FakeDuneCommandManager(
        client,
        notifications,
        tmp_path,
        results=[
            (0, "workspace", ""),
            (0, "-topfile Demo.v", ""),
            (0, "-topfile Demo.v", noise),
            (0, "-topfile Demo.v", noise),
        ],
    )

    first = await manager.check_file(str(source))
    second = await manager.check_file(str(source))

    assert first.status == "complete"
    assert first.restarted is False
    assert second.status == "complete"
    assert second.restarted is False
    assert client.killed is False
    assert client.opened == [(source.resolve(), 1)]
    assert client.changed == [(source.resolve(), 2)]


@pytest.mark.asyncio
async def test_run_at_triggers_dune_build(tmp_path: Path) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.run_at(str(source), "Check nat.", 1)

    assert result.status == "complete"
    assert manager.dune_build_paths == [source.resolve()]


@pytest.mark.asyncio
async def test_run_at_dune_build_failure_returns_failed(tmp_path: Path) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(
        client,
        notifications,
        tmp_path,
        dune_build_result=DuneBuildResult(message="dune failed"),
    )

    result = await manager.run_at(str(source), "Check nat.", 1)

    assert result.status == "failed"
    assert result.message == "dune failed"
    assert result.workspace == str(tmp_path)
    assert client.run_at_requests == []
    assert client.opened == []


@pytest.mark.asyncio
async def test_run_at_dune_build_restarts_existing_process(tmp_path: Path) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(
        client,
        notifications,
        tmp_path,
        dune_build_result=DuneBuildResult(applied=True),
    )

    first = await manager.run_at(str(source), "Check nat.", 1)
    second = await manager.run_at(str(source), "Check nat.", 1)

    assert first.status == "complete"
    assert first.restarted is False
    assert second.status == "complete"
    assert second.restarted is True
    assert manager.dune_build_paths == [source.resolve(), source.resolve()]
    assert client.killed is True


@pytest.mark.asyncio
async def test_check_file_dune_detection_caches_coq_fallback(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeDuneCommandManager(
        client,
        notifications,
        tmp_path,
        results=[
            (0, "workspace", ""),
            (1, "", "unknown command rocq"),
            (0, "-topfile Demo.v", ""),
            (0, "-topfile Demo.v", ""),
            (0, "-topfile Demo.v", ""),
        ],
    )

    first = await manager.check_file(str(source))
    second = await manager.check_file(str(source))

    assert first.status == "complete"
    assert second.status == "complete"
    assert manager.dune_commands == [
        (tmp_path, ("dune", "describe", "workspace")),
        (
            tmp_path,
            (
                "dune",
                "rocq",
                "top",
                "--no-build",
                "--toplevel",
                "echo",
                "Demo.v",
            ),
        ),
        (
            tmp_path,
            (
                "dune",
                "coq",
                "top",
                "--no-build",
                "--toplevel",
                "echo",
                "Demo.v",
            ),
        ),
        (
            tmp_path,
            (
                "dune",
                "coq",
                "top",
                "Demo.v",
                "--toplevel",
                "echo",
                "--display=short",
            ),
        ),
        (
            tmp_path,
            (
                "dune",
                "coq",
                "top",
                "Demo.v",
                "--toplevel",
                "echo",
                "--display=short",
            ),
        ),
    ]


@pytest.mark.asyncio
async def test_check_file_keeps_only_first_error_per_proof_name_and_global_errors(
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

    result = await manager.check_file(str(source))

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
        "first bar",
        "global first",
    ]
    assert [
        diagnostic["message"]
        for diagnostic in result.render(RenderOptions(severity="info"))["diagnostics"]
    ] == [
        "first foo",
        "foo warning",
        "first bar",
        "global first",
    ]


@pytest.mark.asyncio
async def test_check_file_accepts_requested_message_limit(tmp_path: Path) -> None:
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

    result = await manager.check_file(str(source))

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
    assert result.render(
        RenderOptions(message_limit=2, severity="info")
    )["omitted_counts"] == {
        "errors": 1,
        "warnings": 1,
        "infos": 1,
    }


@pytest.mark.asyncio
async def test_check_file_forwards_overall_timeout_override(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(client, notifications, tmp_path)

    await manager.check_file(str(source), overall_timeout_seconds=2.5)
    await manager.check_file(str(source), overall_timeout_seconds=9999)

    assert client.get_document_requests[0]["timeout"] == 2.5
    assert (
        client.get_document_requests[1]["timeout"]
        == MAX_OVERALL_TIMEOUT_SECONDS
    )


@pytest.mark.asyncio
async def test_check_file_can_disable_timeouts(tmp_path: Path) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(client, notifications, tmp_path, command_seconds=5)

    await manager.check_file(
        str(source),
        command_timeout_seconds=0,
        overall_timeout_seconds=0,
        long_running_seconds=0,
    )

    assert client.get_document_requests[0]["timeout"] is None
    assert client.get_document_requests[0]["command_timeout"] is None


@pytest.mark.asyncio
async def test_check_file_reports_long_running_commands(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Goal True.\nProof.\n  sleep 5.\nQed.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(
        notifications,
        file_perf_data=file_perf_data(
            source.as_uri(),
            1,
            line=2,
            start_character=2,
            end_character=10,
            seconds=5,
        ),
    )
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.check_file(str(source), long_running_seconds=3)

    rendered = result.render()
    assert rendered["long_running_commands"] == [
        {
            "location": "3:3 to 3:11",
            "text": "sleep 5.",
        }
    ]


@pytest.mark.asyncio
async def test_check_file_disables_long_running_commands(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Goal True.\nProof.\n  sleep 5.\nQed.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(
        notifications,
        file_perf_data=file_perf_data(
            source.as_uri(),
            1,
            line=2,
            start_character=2,
            end_character=10,
            seconds=5,
        ),
    )
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.check_file(str(source), long_running_seconds=0)

    assert "long_running_commands" not in result.render()


@pytest.mark.asyncio
async def test_check_file_ignores_cached_long_running_commands(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Goal True.\nProof.\n  sleep 5.\nQed.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(
        notifications,
        file_perf_data=file_perf_data(
            source.as_uri(),
            1,
            line=2,
            start_character=2,
            end_character=10,
            seconds=5,
            cache_hit=True,
        ),
    )
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.check_file(str(source), long_running_seconds=3)

    assert "long_running_commands" not in result.render()


@pytest.mark.asyncio
async def test_check_file_enriches_incomplete_proof_save_error(
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

    result = await manager.check_file(str(source), overall_timeout_seconds=2.5)

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
async def test_check_file_skips_goal_request_for_regular_proof_error(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Goal 0 = 0. Proof. fail. Qed.\n", encoding="utf-8")
    range_ = {
        "start": {"line": 0, "character": 19},
        "end": {"line": 0, "character": 24},
    }
    notifications = SpyNotificationStore()
    client = FakeClient(
        notifications,
        [
            {
                "range": range_,
                "message": "Tactic failure.",
                "severity": 1,
                "data": {"sentenceRange": range_, "proofName": "foo"},
            }
        ],
    )
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.check_file(str(source))

    assert result.error_count == 1
    assert result.diagnostics[0].sentence == "fail."
    assert client.goal_requests == []


@pytest.mark.asyncio
async def test_check_file_adds_timeout_context_from_file_progress(
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

    result = await manager.check_file(str(source), overall_timeout_seconds=2.5)

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
async def test_check_file_prefers_completed_position_for_timeout_context(
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

    result = await manager.check_file(str(source))

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
async def test_run_at_forwards_position_and_returns_feedback(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("From Stdlib Require Import Arith.PeanoNat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(
        notifications,
        run_at_response={
            "feedback": [[3, "Nat.add_0_r: forall n : nat, n + 0 = n"]],
        },
    )
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.run_at(str(source), "Search (_ + 0 = _).", 1, 2)

    assert result.status == "complete"
    assert result.version == 1
    assert result.location == "1:2"
    assert result.output[0].render() == {
        "level": "info",
        "text": "Nat.add_0_r: forall n : nat, n + 0 = n",
    }
    assert client.opened == [(source.resolve(), 1)]
    assert client.run_at_requests == [
        {
            "uri": source.as_uri(),
            "version": 1,
            "position": {"line": 0, "character": 1},
            "commands": "Search (_ + 0 = _).",
            "timeout": 1,
            "hard_timeout_grace": 0.01,
        }
    ]


@pytest.mark.asyncio
async def test_run_at_forwards_overall_timeout_override(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(client, notifications, tmp_path)

    await manager.run_at(
        str(source),
        "Search nat.",
        1,
        1,
        overall_timeout_seconds=2.5,
    )
    await manager.run_at(
        str(source),
        "Search nat.",
        1,
        1,
        overall_timeout_seconds=9999,
    )

    assert client.run_at_requests[0]["timeout"] == 2.5
    assert client.run_at_requests[1]["timeout"] == MAX_OVERALL_TIMEOUT_SECONDS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("commands", "line", "character", "message"),
    [
        ("", 1, 1, "commands must be a non-empty Rocq command string ending in '.'"),
        (
            "Check nat",
            1,
            1,
            "commands must be a non-empty Rocq command string ending in '.'",
        ),
        ("Check nat.", 0, 1, "line and character must be positive 1-based integers"),
        ("Check nat.", 1, 0, "line and character must be positive 1-based integers"),
    ],
)
async def test_run_at_validates_before_lsp_request(
    tmp_path: Path,
    commands: str,
    line: int,
    character: int,
    message: str,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.run_at(str(source), commands, line, character)

    assert result.status == "failed"
    assert result.message == message
    assert client.opened == []
    assert client.changed == []
    assert client.run_at_requests == []


@pytest.mark.asyncio
async def test_run_at_response_error_includes_feedback(tmp_path: Path) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(
        notifications,
        run_at_response=RocqLspResponseError(
            {
                "message": "Coq: command failed",
                "data": {"feedback": [[3, "partial output"]]},
            }
        ),
    )
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.run_at(str(source), "Check missing.", 1, 1)

    assert result.status == "failed"
    assert result.message == "Coq: command failed"
    assert result.output[0].render() == {"level": "info", "text": "partial output"}


@pytest.mark.asyncio
async def test_run_at_hard_timeout_resets_session(tmp_path: Path) -> None:
    source = tmp_path / "Slow.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    client.hard_timeout = True
    manager = FakeManager(client, notifications, tmp_path)

    result = await manager.run_at(str(source), "Search nat.", 1, 1)

    assert result.status == "timeout"
    assert result.timed_out is True
    assert result.restarted is True
    assert client.pending_cleared is True
    assert client.killed is True
    assert source.resolve() not in manager._sessions


@pytest.mark.asyncio
async def test_check_file_adds_command_timeout_context(tmp_path: Path) -> None:
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

    result = await manager.check_file(str(source), command_timeout_seconds=2)

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
async def test_check_file_passes_command_timeout_after_error_to_client(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(
        client, notifications, tmp_path, command_seconds_after_error=2.5
    )

    await manager.check_file(str(source))

    assert client.get_document_requests[0]["command_timeout_after_error"] == 2.5


@pytest.mark.asyncio
async def test_check_file_disables_non_positive_command_timeout_after_error(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    for policy_value in (0, -1):
        notifications = SpyNotificationStore()
        client = FakeClient(notifications)
        manager = FakeManager(
            client, notifications, tmp_path, command_seconds_after_error=policy_value
        )

        await manager.check_file(str(source))

        assert client.get_document_requests[0]["command_timeout_after_error"] is None


@pytest.mark.asyncio
async def test_check_file_command_timeout_after_error_override(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(
        client, notifications, tmp_path, command_seconds_after_error=2
    )

    await manager.check_file(str(source), command_timeout_after_error_seconds=1.5)
    await manager.check_file(str(source), command_timeout_after_error_seconds=0)

    assert client.get_document_requests[0]["command_timeout_after_error"] == 1.5
    assert client.get_document_requests[1]["command_timeout_after_error"] is None


@pytest.mark.asyncio
async def test_check_file_disables_non_positive_policy_command_timeout(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(client, notifications, tmp_path, command_seconds=0)

    await manager.check_file(str(source))

    assert client.get_document_requests[0]["command_timeout"] is None


@pytest.mark.asyncio
async def test_check_file_adds_command_timeout_after_error_context(
    tmp_path: Path,
) -> None:
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
        timeout_kind="command_after_error",
        timed_out_range=timed_out_range,
    )
    manager = FakeManager(
        client, notifications, tmp_path, command_seconds_after_error=2
    )

    result = await manager.check_file(str(source))

    assert result.status == "timeout"
    assert result.render()["timeout_context"] == {
        "operation": "coq/getDocument",
        "timeout_kind": "command_after_error",
        "request_timeout_seconds": 1.0,
        "cancel_grace_seconds": 0.01,
        "checked_until": "3:3",
        "latest_processing_range": "3:3 to 5:5",
        "next_unchecked": {"location": "3:3", "line": "sleep 5."},
        "command_timeout_after_error_seconds": 2,
        "timed_out_range": "3:3 to 3:11",
        "timed_out_command": {"location": "3:3", "line": "sleep 5."},
    }


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

    result = await manager.check_file(str(source))

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
    assert source.resolve() not in manager._sessions


@pytest.mark.asyncio
async def test_check_file_external_cancellation_resets_stuck_session(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Slow.v"
    source.write_text("Goal True.\nProof.\n  sleep 5.\nQed.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(client, notifications, tmp_path)

    async def cancelled_get_document(**kwargs: Any) -> GetDocumentResult:
        client.restart_required_after_external_cancellation = True
        raise asyncio.CancelledError

    client.request_get_document = cancelled_get_document  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await manager.check_file(str(source))

    assert client.pending_cleared is True
    assert client.killed is True
    assert source.resolve() not in manager._sessions


@pytest.mark.asyncio
async def test_run_at_external_cancellation_resets_stuck_session(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Slow.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(client, notifications, tmp_path)

    async def cancelled_run_at_point(**kwargs: Any) -> tuple[dict[str, Any], bool]:
        client.restart_required_after_external_cancellation = True
        raise asyncio.CancelledError

    client.request_run_at_point = cancelled_run_at_point  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await manager.run_at(str(source), "Search nat.", 1, 1)

    assert client.pending_cleared is True
    assert client.killed is True
    assert source.resolve() not in manager._sessions


@pytest.mark.asyncio
async def test_check_file_uses_separate_sessions_for_files_in_same_project(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "_RocqProject").write_text("-Q . Demo\n", encoding="utf-8")
    source_a = project / "A.v"
    source_b = project / "B.v"
    source_a.write_text("Check nat.\n", encoding="utf-8")
    source_b.write_text("Check nat.\n", encoding="utf-8")
    notifications = SpyNotificationStore()
    client = FakeClient(notifications)
    manager = FakeManager(client, notifications, None)

    await manager.check_file(str(source_a))
    await manager.check_file(str(source_b))

    assert set(manager._sessions) == {source_a.resolve(), source_b.resolve()}
    assert {session.workspace for session in manager._sessions.values()} == {
        project.resolve()
    }
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
    stale_file = stale_workspace / "Stale.v"
    fresh_file = fresh_workspace / "Fresh.v"
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
    manager._sessions[stale_file] = stale_session
    manager._sessions[fresh_file] = fresh_session

    await manager._cleanup_idle_sessions()

    assert stale_file not in manager._sessions
    assert fresh_file in manager._sessions
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
    source = workspace / "Locked.v"
    client = FakeClient(notifications)
    session = FakeSession(
        workspace=workspace,
        config=manager.config,
        client=client,
        notifications=notifications,
    )
    session.last_used_at = time.monotonic() - 2
    manager._sessions[source] = session
    await session.lock.acquire()

    try:
        await manager._cleanup_idle_sessions()
    finally:
        session.lock.release()

    assert manager._sessions[source] is session
    assert client.closed is False


@pytest.mark.asyncio
async def test_checkout_retries_when_waiting_on_reset_session(tmp_path: Path) -> None:
    notifications = SpyNotificationStore()
    manager = FakeManager(FakeClient(notifications), notifications, tmp_path)
    source = tmp_path / "Demo.v"
    original, original_created = await manager._checkout_session(source, tmp_path)
    assert original_created is True

    waiting = asyncio.create_task(manager._checkout_session(source, tmp_path))
    while original.active_requests < 2:
        await asyncio.sleep(0)

    await manager._reset_session(source)
    original.lock.release()
    await manager._release_session(original)
    replacement, replacement_created = await asyncio.wait_for(waiting, timeout=1)

    try:
        assert replacement is not original
        assert replacement_created is True
        assert manager._sessions[source] is replacement
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
