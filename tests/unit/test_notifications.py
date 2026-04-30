from __future__ import annotations

import asyncio

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


def file_perf_data(version: int | None = 3) -> dict[str, object]:
    text_document: dict[str, object] = {"uri": "file:///Demo.v"}
    if version is not None:
        text_document["version"] = version
    return {
        "textDocument": text_document,
        "summary": "",
        "timings": [
            {
                "range": {
                    "start": {"line": 1, "character": 2},
                    "end": {"line": 1, "character": 10},
                },
                "info": {
                    "time": 4.5,
                    "memory": 0,
                    "cache_hit": False,
                    "time_hash": 0.0,
                },
            }
        ],
    }


def execution_info(version: int | None = 3) -> dict[str, object]:
    text_document: dict[str, object] = {"uri": "file:///Demo.v"}
    if version is not None:
        text_document["version"] = version
    return {
        "textDocument": text_document,
        "range": {
            "start": {"line": 4, "character": 2},
            "end": {"line": 4, "character": 10},
        },
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
async def test_notification_store_returns_versioned_file_perf_data() -> None:
    store = NotificationStore()
    perf_data = file_perf_data(version=3)

    store.handle("$/coq/filePerfData", perf_data)

    assert store.latest_file_perf_data("file:///Demo.v", 3) == perf_data
    assert store.latest_file_perf_data("file:///Demo.v", 4) is None
    assert (
        await store.wait_for_file_perf_data(
            "file:///Demo.v",
            3,
            settle_seconds=0,
        )
        == perf_data
    )


def test_notification_store_falls_back_to_unversioned_file_perf_data() -> None:
    store = NotificationStore()
    perf_data = file_perf_data(version=None)

    store.handle("$/coq/filePerfData", perf_data)

    assert store.latest_file_perf_data("file:///Demo.v", 3) == perf_data


def test_notification_store_clears_versioned_file_perf_data() -> None:
    store = NotificationStore()
    perf_data = file_perf_data(version=3)

    store.handle("$/coq/filePerfData", perf_data)
    store.clear("file:///Demo.v", 3)

    assert store.latest_file_perf_data("file:///Demo.v", 3) is None


def test_notification_store_replaces_stale_file_perf_data_versions() -> None:
    store = NotificationStore()
    first = file_perf_data(version=3)
    unversioned = file_perf_data(version=None)
    second = file_perf_data(version=4)

    store.handle("$/coq/filePerfData", first)
    store.handle("$/coq/filePerfData", unversioned)

    assert set(store._file_perf_data) == {("file:///Demo.v", None)}
    assert store.latest_file_perf_data("file:///Demo.v", 4) == unversioned

    store.handle("$/coq/filePerfData", second)

    assert set(store._file_perf_data) == {("file:///Demo.v", 4)}
    assert store.latest_file_perf_data("file:///Demo.v", 4) == second
    assert store.latest_file_perf_data("file:///Demo.v", 5) is None


@pytest.mark.asyncio
async def test_notification_store_returns_versioned_execution_info() -> None:
    store = NotificationStore()
    info = execution_info(version=3)

    store.handle("$/coq/executionInformation", info)

    sequence = store.latest_execution_sequence("file:///Demo.v", 3)
    assert sequence == 1
    assert store.latest_execution_info("file:///Demo.v", 3) == info
    assert store.execution_ranges("file:///Demo.v", 3) == [info["range"]]
    assert store.latest_execution_info("file:///Demo.v", 4) is None
    assert await store.wait_for_execution_info(
        "file:///Demo.v",
        3,
        after_sequence=0,
        timeout=0,
    ) == (sequence, info)


@pytest.mark.asyncio
async def test_notification_store_waits_for_new_execution_info() -> None:
    store = NotificationStore()
    first = execution_info(version=3)
    second = execution_info(version=3)
    second["range"] = {
        "start": {"line": 5, "character": 2},
        "end": {"line": 5, "character": 7},
    }

    store.handle("$/coq/executionInformation", first)
    first_sequence = store.latest_execution_sequence("file:///Demo.v", 3)

    async def publish_second() -> None:
        await asyncio.sleep(0)
        store.handle("$/coq/executionInformation", second)

    task = asyncio.create_task(publish_second())
    try:
        assert await store.wait_for_execution_info(
            "file:///Demo.v",
            3,
            after_sequence=first_sequence,
            timeout=1,
        ) == (first_sequence + 1, second)
    finally:
        await task


def test_notification_store_clears_versioned_execution_info() -> None:
    store = NotificationStore()

    store.handle("$/coq/executionInformation", execution_info(version=3))
    store.clear("file:///Demo.v", 3)

    assert store.latest_execution_sequence("file:///Demo.v", 3) == 0
    assert store.latest_execution_info("file:///Demo.v", 3) is None
    assert store.execution_ranges("file:///Demo.v", 3) == []


def test_notification_store_replaces_stale_execution_versions() -> None:
    store = NotificationStore()
    first = execution_info(version=3)
    second = execution_info(version=4)

    store.handle("$/coq/executionInformation", first)
    store.handle("$/coq/executionInformation", second)

    assert store.latest_execution_info("file:///Demo.v", 3) is None
    assert store.execution_ranges("file:///Demo.v", 3) == []
    assert store.latest_execution_info("file:///Demo.v", 4) == second
    assert store.execution_ranges("file:///Demo.v", 4) == [second["range"]]


@pytest.mark.asyncio
async def test_notification_store_ignores_unrelated_notifications() -> None:
    store = NotificationStore()

    store.handle("$/coq/filePerfData", {"textDocument": {"uri": 3}, "timings": []})
    store.handle("textDocument/publishDiagnostics", {"uri": 3, "diagnostics": []})
    store.handle("$/coq/fileProgress", {"textDocument": {"uri": 3}})
    store.handle("$/coq/executionInformation", {"textDocument": {"uri": 3}})

    assert await store.wait_for_diagnostics("file:///Demo.v", 1, settle_seconds=0) == []
    assert store.latest_file_progress("file:///Demo.v", 1) is None
    assert store.latest_file_perf_data("file:///Demo.v", 1) is None
    assert store.latest_execution_info("file:///Demo.v", 1) is None
