from __future__ import annotations

import pytest

from rocq_lsp_mcp.notifications import NotificationStore


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


@pytest.mark.asyncio
async def test_notification_store_ignores_unrelated_notifications() -> None:
    store = NotificationStore()

    store.handle("$/coq/filePerfData", {"uri": "file:///Demo.v"})
    store.handle("textDocument/publishDiagnostics", {"uri": 3, "diagnostics": []})

    assert await store.wait_for_diagnostics("file:///Demo.v", 1, settle_seconds=0) == []
