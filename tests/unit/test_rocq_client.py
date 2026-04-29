from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from rocq_lsp_mcp.notifications import NotificationStore
from rocq_lsp_mcp.rocq_client import RocqLspClient, RocqLspHardTimeout


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
