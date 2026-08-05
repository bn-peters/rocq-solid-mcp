from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from rocq_lsp_mcp.notifications import NotificationStore
from rocq_lsp_mcp.rocq_client import (
    RocqLspClient,
    RocqLspHardTimeout,
    RocqLspProcessExited,
    RocqLspUnavailableError,
)


def client_with_store(tmp_path: Path, store: NotificationStore) -> RocqLspClient:
    return RocqLspClient(
        binary="coq-lsp",
        use_opam=False,
        workspace=tmp_path,
        max_errors=1000000,
        request_timeout=5,
        notifications=store,
    )


def execution_info(uri: str, version: int, line: int) -> dict[str, Any]:
    return {
        "textDocument": {"uri": uri, "version": version},
        "range": {
            "start": {"line": line, "character": 2},
            "end": {"line": line, "character": 10},
        },
    }


def error_diagnostics(uri: str, version: int) -> dict[str, Any]:
    return {
        "uri": uri,
        "version": version,
        "diagnostics": [
            {
                "message": "bad",
                "severity": 1,
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 4},
                },
            }
        ],
    }


def completed_document() -> dict[str, Any]:
    return {
        "completed": {
            "status": "Yes",
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 2, "character": 0},
            },
        }
    }


@pytest.mark.asyncio
async def test_start_reports_missing_direct_binary(tmp_path: Path) -> None:
    store = NotificationStore()
    client = RocqLspClient(
        binary="definitely-not-a-coq-lsp-binary",
        use_opam=False,
        workspace=tmp_path,
        max_errors=1000000,
        request_timeout=5,
        notifications=store,
    )

    with pytest.raises(RocqLspUnavailableError) as exc_info:
        await client.start()

    message = str(exc_info.value)
    assert "required executable 'definitely-not-a-coq-lsp-binary'" in message
    assert "Tried command: definitely-not-a-coq-lsp-binary" in message
    assert "ROCQ_LSP_BINARY" in message


@pytest.mark.asyncio
async def test_start_reports_missing_opam_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")
    store = NotificationStore()
    client = RocqLspClient(
        binary="coq-lsp",
        use_opam=True,
        workspace=tmp_path,
        max_errors=1000000,
        request_timeout=5,
        notifications=store,
    )

    with pytest.raises(RocqLspUnavailableError) as exc_info:
        await client.start()

    message = str(exc_info.value)
    assert "required executable 'opam'" in message
    assert "Tried command: opam exec -- coq-lsp" in message
    assert "ROCQ_LSP_USE_OPAM=0" in message


@pytest.mark.asyncio
async def test_request_get_document_does_not_timeout_when_commands_advance(
    tmp_path: Path,
) -> None:
    store = NotificationStore()
    client = client_with_store(tmp_path, store)
    sent: list[dict[str, Any]] = []
    uri = "file:///Demo.v"
    version = 1

    async def fake_send(message: dict[str, Any]) -> None:
        sent.append(message)
        if message.get("method") != "coq/getDocument":
            return
        request_id = message["id"]

        async def finish() -> None:
            await asyncio.sleep(0)
            store.handle("$/coq/executionInformation", execution_info(uri, version, 0))
            await asyncio.sleep(0.01)
            store.handle("$/coq/executionInformation", execution_info(uri, version, 1))
            await asyncio.sleep(0.01)
            client._pending[request_id].set_result(
                {"jsonrpc": "2.0", "id": request_id, "result": completed_document()}
            )

        asyncio.create_task(finish())

    client._send = fake_send  # type: ignore[method-assign]

    result = await client.request_get_document(
        uri=uri,
        version=version,
        timeout=1,
        hard_timeout_grace=0.1,
        command_timeout=0.1,
    )

    assert result.document == completed_document()
    assert result.timed_out is False
    assert not any(message.get("method") == "$/cancelRequest" for message in sent)


@pytest.mark.asyncio
async def test_request_get_document_cancels_when_command_timeout_expires(
    tmp_path: Path,
) -> None:
    store = NotificationStore()
    client = client_with_store(tmp_path, store)
    sent: list[dict[str, Any]] = []
    uri = "file:///Demo.v"
    version = 1

    async def fake_send(message: dict[str, Any]) -> None:
        sent.append(message)
        method = message.get("method")
        if method == "coq/getDocument":
            async def publish_execution_info() -> None:
                await asyncio.sleep(0)
                store.handle(
                    "$/coq/executionInformation",
                    execution_info(uri, version, 3),
                )

            asyncio.create_task(publish_execution_info())
        if method == "$/cancelRequest":
            request_id = message["params"]["id"]
            client._pending[request_id].set_result(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32800, "message": "Cancelled by client"},
                }
            )

    client._send = fake_send  # type: ignore[method-assign]

    result = await client.request_get_document(
        uri=uri,
        version=version,
        timeout=1,
        hard_timeout_grace=0.1,
        command_timeout=0.01,
    )

    assert result.timed_out is True
    assert result.timeout_kind == "command"
    assert result.timed_out_range == execution_info(uri, version, 3)["range"]
    assert any(message.get("method") == "$/cancelRequest" for message in sent)


@pytest.mark.asyncio
async def test_request_get_document_prefers_completed_result_over_watchdog_race(
    tmp_path: Path,
) -> None:
    store = NotificationStore()
    client = client_with_store(tmp_path, store)
    sent: list[dict[str, Any]] = []
    uri = "file:///Demo.v"
    version = 1

    async def fake_send(message: dict[str, Any]) -> None:
        sent.append(message)
        method = message.get("method")
        if method == "coq/getDocument":
            async def publish_execution_info() -> None:
                await asyncio.sleep(0)
                store.handle(
                    "$/coq/executionInformation",
                    execution_info(uri, version, 3),
                )

            asyncio.create_task(publish_execution_info())
        if method == "$/cancelRequest":
            request_id = message["params"]["id"]
            client._pending[request_id].set_result(
                {"jsonrpc": "2.0", "id": request_id, "result": completed_document()}
            )

    client._send = fake_send  # type: ignore[method-assign]

    result = await client.request_get_document(
        uri=uri,
        version=version,
        timeout=1,
        hard_timeout_grace=0.1,
        command_timeout=0.01,
    )

    assert result.document == completed_document()
    assert result.timed_out is False
    assert any(message.get("method") == "$/cancelRequest" for message in sent)


@pytest.mark.asyncio
async def test_request_get_document_does_not_command_timeout_final_sentence(
    tmp_path: Path,
) -> None:
    store = NotificationStore()
    client = client_with_store(tmp_path, store)
    sent: list[dict[str, Any]] = []
    uri = "file:///Demo.v"
    version = 1
    content = "End acc.\nCheck id.\n"

    async def fake_send(message: dict[str, Any]) -> None:
        sent.append(message)
        if message.get("method") != "coq/getDocument":
            return
        request_id = message["id"]

        async def finish() -> None:
            await asyncio.sleep(0)
            store.handle(
                "$/coq/executionInformation",
                {
                    "textDocument": {"uri": uri, "version": version},
                    "range": {
                        "start": {"line": 1, "character": 0},
                        "end": {"line": 1, "character": 9},
                    },
                },
            )
            await asyncio.sleep(0.03)
            client._pending[request_id].set_result(
                {"jsonrpc": "2.0", "id": request_id, "result": completed_document()}
            )

        asyncio.create_task(finish())

    client._send = fake_send  # type: ignore[method-assign]

    result = await client.request_get_document(
        uri=uri,
        version=version,
        timeout=1,
        hard_timeout_grace=0.1,
        command_timeout=0.01,
        content=content,
    )

    assert result.document == completed_document()
    assert result.timed_out is False
    assert not any(message.get("method") == "$/cancelRequest" for message in sent)


@pytest.mark.asyncio
async def test_request_get_document_after_error_deadline_cancels(
    tmp_path: Path,
) -> None:
    store = NotificationStore()
    client = client_with_store(tmp_path, store)
    sent: list[dict[str, Any]] = []
    uri = "file:///Demo.v"
    version = 1

    async def fake_send(message: dict[str, Any]) -> None:
        sent.append(message)
        method = message.get("method")
        if method == "coq/getDocument":
            async def publish() -> None:
                await asyncio.sleep(0)
                store.handle(
                    "$/coq/executionInformation",
                    execution_info(uri, version, 5),
                )
                store.handle(
                    "textDocument/publishDiagnostics",
                    error_diagnostics(uri, version),
                )

            asyncio.create_task(publish())
        if method == "$/cancelRequest":
            request_id = message["params"]["id"]
            client._pending[request_id].set_result(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32800, "message": "Cancelled by client"},
                }
            )

    client._send = fake_send  # type: ignore[method-assign]

    result = await client.request_get_document(
        uri=uri,
        version=version,
        timeout=1,
        hard_timeout_grace=0.1,
        timeout_after_error=0.02,
    )

    assert result.timed_out is True
    assert result.timeout_kind == "after_error"
    assert result.timed_out_range == execution_info(uri, version, 5)["range"]


@pytest.mark.asyncio
async def test_request_get_document_per_sentence_timeout_still_applies_after_error(
    tmp_path: Path,
) -> None:
    store = NotificationStore()
    client = client_with_store(tmp_path, store)
    sent: list[dict[str, Any]] = []
    uri = "file:///Demo.v"
    version = 1

    async def fake_send(message: dict[str, Any]) -> None:
        sent.append(message)
        method = message.get("method")
        if method == "coq/getDocument":
            async def publish() -> None:
                await asyncio.sleep(0)
                store.handle(
                    "textDocument/publishDiagnostics",
                    error_diagnostics(uri, version),
                )
                store.handle(
                    "$/coq/executionInformation",
                    execution_info(uri, version, 3),
                )

            asyncio.create_task(publish())
        if method == "$/cancelRequest":
            request_id = message["params"]["id"]
            client._pending[request_id].set_result(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32800, "message": "Cancelled by client"},
                }
            )

    client._send = fake_send  # type: ignore[method-assign]

    # The post-error sentence exceeds the per-sentence watchdog long before
    # the distant after-error deadline: the cancel must be a "command" one.
    result = await client.request_get_document(
        uri=uri,
        version=version,
        timeout=1,
        hard_timeout_grace=0.1,
        command_timeout=0.02,
        timeout_after_error=10,
    )

    assert result.timed_out is True
    assert result.timeout_kind == "command"
    assert result.timed_out_range == execution_info(uri, version, 3)["range"]


@pytest.mark.asyncio
async def test_request_get_document_error_wake_does_not_restart_sentence_timer(
    tmp_path: Path,
) -> None:
    store = NotificationStore()
    client = client_with_store(tmp_path, store)
    uri = "file:///Demo.v"
    version = 1
    command_timeout = 0.4
    error_delay = 0.3

    async def fake_send(message: dict[str, Any]) -> None:
        method = message.get("method")
        if method == "coq/getDocument":
            async def publish() -> None:
                await asyncio.sleep(0)
                store.handle(
                    "$/coq/executionInformation",
                    execution_info(uri, version, 3),
                )
                await asyncio.sleep(error_delay)
                store.handle(
                    "textDocument/publishDiagnostics",
                    error_diagnostics(uri, version),
                )

            asyncio.create_task(publish())
        if method == "$/cancelRequest":
            request_id = message["params"]["id"]
            client._pending[request_id].set_result(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32800, "message": "Cancelled by client"},
                }
            )

    client._send = fake_send  # type: ignore[method-assign]

    loop = asyncio.get_running_loop()
    started = loop.time()
    result = await client.request_get_document(
        uri=uri,
        version=version,
        timeout=2,
        hard_timeout_grace=0.1,
        command_timeout=command_timeout,
        timeout_after_error=10,
    )
    elapsed = loop.time() - started

    # The per-sentence deadline is anchored to the sentence start; the error
    # wake mid-sentence must not grant the sentence a fresh window, so the
    # cancel lands at ~command_timeout, not ~error_delay + command_timeout.
    assert result.timed_out is True
    assert result.timeout_kind == "command"
    assert elapsed < error_delay + command_timeout - 0.1


@pytest.mark.asyncio
async def test_request_get_document_contains_watchdog_send_failure(
    tmp_path: Path,
) -> None:
    store = NotificationStore()
    client = client_with_store(tmp_path, store)
    uri = "file:///Demo.v"
    version = 1

    async def fake_send(message: dict[str, Any]) -> None:
        method = message.get("method")
        if method == "coq/getDocument":
            async def publish() -> None:
                await asyncio.sleep(0)
                store.handle(
                    "textDocument/publishDiagnostics",
                    error_diagnostics(uri, version),
                )

            asyncio.create_task(publish())
        if method == "$/cancelRequest":
            raise RocqLspProcessExited("coq-lsp is not running")

    client._send = fake_send  # type: ignore[method-assign]

    # The watchdog publishes its outcome before sending the cancel and
    # contains the send failure, so the classified timeout survives as a
    # hard timeout instead of the send error clobbering the result.
    with pytest.raises(RocqLspHardTimeout) as exc_info:
        await client.request_get_document(
            uri=uri,
            version=version,
            timeout=1,
            hard_timeout_grace=0.05,
            timeout_after_error=0.02,
        )

    assert exc_info.value.timeout_kind == "after_error"


@pytest.mark.asyncio
async def test_request_get_document_hard_timeout_preserves_command_context(
    tmp_path: Path,
) -> None:
    store = NotificationStore()
    client = client_with_store(tmp_path, store)
    uri = "file:///Demo.v"
    version = 1

    async def fake_send(message: dict[str, Any]) -> None:
        if message.get("method") == "coq/getDocument":
            async def publish_execution_info() -> None:
                await asyncio.sleep(0)
                store.handle(
                    "$/coq/executionInformation",
                    execution_info(uri, version, 4),
                )

            asyncio.create_task(publish_execution_info())

    client._send = fake_send  # type: ignore[method-assign]

    with pytest.raises(RocqLspHardTimeout) as exc_info:
        await client.request_get_document(
            uri=uri,
            version=version,
            timeout=1,
            hard_timeout_grace=0.01,
            command_timeout=0.01,
        )

    assert exc_info.value.timeout_kind == "command"
    assert exc_info.value.timed_out_range == execution_info(uri, version, 4)["range"]


@pytest.mark.asyncio
async def test_request_get_document_external_cancellation_sends_cancel(
    tmp_path: Path,
) -> None:
    store = NotificationStore()
    client = client_with_store(tmp_path, store)
    sent: list[dict[str, Any]] = []
    uri = "file:///Demo.v"
    version = 1

    async def fake_send(message: dict[str, Any]) -> None:
        sent.append(message)
        if message.get("method") == "$/cancelRequest":
            request_id = message["params"]["id"]
            client._pending[request_id].set_result(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32800, "message": "Cancelled by client"},
                }
            )

    client._send = fake_send  # type: ignore[method-assign]

    task = asyncio.create_task(
        client.request_get_document(
            uri=uri,
            version=version,
            timeout=10,
            hard_timeout_grace=0.1,
        )
    )
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert any(message.get("method") == "$/cancelRequest" for message in sent)
    assert client._pending == {}
    assert client.restart_required_after_external_cancellation is False


@pytest.mark.asyncio
async def test_request_get_document_external_cancellation_marks_stuck_session(
    tmp_path: Path,
) -> None:
    store = NotificationStore()
    client = client_with_store(tmp_path, store)
    sent: list[dict[str, Any]] = []
    uri = "file:///Demo.v"
    version = 1

    async def fake_send(message: dict[str, Any]) -> None:
        sent.append(message)

    client._send = fake_send  # type: ignore[method-assign]

    task = asyncio.create_task(
        client.request_get_document(
            uri=uri,
            version=version,
            timeout=10,
            hard_timeout_grace=0.01,
        )
    )
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert any(message.get("method") == "$/cancelRequest" for message in sent)
    assert client._pending == {}
    assert client.restart_required_after_external_cancellation is True
