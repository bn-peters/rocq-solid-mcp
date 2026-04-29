from __future__ import annotations

import pytest

from rocq_lsp_mcp.notifications import NotificationStore


def file_progress(version: int | None = 3) -> dict[str, object]:
    text_document: dict[str, object] = {"uri": "file:///Demo.v"}
    if version is not None:
        text_document["version"] = version
    return {
        "textDocument": text_document,
        "processing": [
            {
                "range": {
                    "start": {"line": 1, "character": 2},
                    "end": {"line": 3, "character": 4},
                },
                "kind": 1,
            }
        ],
    }


@pytest.mark.asyncio
async def test_notification_store_returns_versioned_diagnostics() -> None:
    store = NotificationStore()
    diagnostics = [{"message": "bad"}]

    store.handle(
        "textDocument/publishDiagnostics",
        {"uri": "file:///Demo.v", "version": 3, "diagnostics": diagnostics},
    )

    assert (
        await store.wait_for_diagnostics("file:///Demo.v", 3, settle_seconds=0)
        == diagnostics
    )


@pytest.mark.asyncio
async def test_notification_store_falls_back_to_unversioned_diagnostics() -> None:
    store = NotificationStore()
    diagnostics = [{"message": "bad"}]

    store.handle(
        "textDocument/publishDiagnostics",
        {"uri": "file:///Demo.v", "diagnostics": diagnostics},
    )

    assert (
        await store.wait_for_diagnostics("file:///Demo.v", 3, settle_seconds=0)
        == diagnostics
    )


@pytest.mark.asyncio
async def test_notification_store_clears_versioned_diagnostics() -> None:
    store = NotificationStore()
    diagnostics = [{"message": "bad"}]

    store.handle(
        "textDocument/publishDiagnostics",
        {"uri": "file:///Demo.v", "version": 3, "diagnostics": diagnostics},
    )
    store.clear("file:///Demo.v", 3)

    assert await store.wait_for_diagnostics("file:///Demo.v", 3, settle_seconds=0) == []


def test_notification_store_returns_versioned_file_progress() -> None:
    store = NotificationStore()
    progress = file_progress(version=3)

    store.handle("$/coq/fileProgress", progress)

    assert store.latest_file_progress("file:///Demo.v", 3) == progress
    assert store.latest_file_progress("file:///Demo.v", 4) is None


def test_notification_store_falls_back_to_unversioned_file_progress() -> None:
    store = NotificationStore()
    progress = file_progress(version=None)

    store.handle("$/coq/fileProgress", progress)

    assert store.latest_file_progress("file:///Demo.v", 3) == progress


def test_notification_store_clears_versioned_file_progress() -> None:
    store = NotificationStore()
    progress = file_progress(version=3)

    store.handle("$/coq/fileProgress", progress)
    store.clear("file:///Demo.v", 3)

    assert store.latest_file_progress("file:///Demo.v", 3) is None


@pytest.mark.asyncio
async def test_notification_store_ignores_unrelated_notifications() -> None:
    store = NotificationStore()

    store.handle("$/coq/filePerfData", {"uri": "file:///Demo.v"})
    store.handle("textDocument/publishDiagnostics", {"uri": 3, "diagnostics": []})
    store.handle("$/coq/fileProgress", {"textDocument": {"uri": 3}})

    assert await store.wait_for_diagnostics("file:///Demo.v", 1, settle_seconds=0) == []
    assert store.latest_file_progress("file:///Demo.v", 1) is None
