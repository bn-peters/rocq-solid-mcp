from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any


class NotificationStore:
    def __init__(self) -> None:
        self._diagnostics: dict[tuple[str, int | None], list[dict[str, Any]]] = {}
        self._diagnostic_events: dict[tuple[str, int | None], asyncio.Event] = {}
        self._latest_versions_by_uri: dict[str, int | None] = {}
        self._file_progress: dict[tuple[str, int | None], dict[str, Any]] = {}
        self._latest_progress_versions_by_uri: dict[str, int | None] = {}
        self._file_perf_data: dict[tuple[str, int | None], dict[str, Any]] = {}
        self._perf_events: dict[tuple[str, int | None], asyncio.Event] = {}
        self._latest_perf_versions_by_uri: dict[str, int | None] = {}
        self._execution_info: dict[
            tuple[str, int | None], tuple[int, dict[str, Any]]
        ] = {}
        self._execution_ranges: dict[tuple[str, int | None], list[dict[str, Any]]] = {}
        self._execution_events: dict[tuple[str, int | None], asyncio.Event] = {}
        self._execution_sequence = 0

    def handle(self, method: str, params: Any | None) -> None:
        if method == "textDocument/publishDiagnostics" and isinstance(params, dict):
            self._handle_publish_diagnostics(params)
        if method == "$/coq/fileProgress" and isinstance(params, dict):
            self._handle_file_progress(params)
        if method == "$/coq/filePerfData" and isinstance(params, dict):
            self._handle_file_perf_data(params)
        if method == "$/coq/executionInformation" and isinstance(params, dict):
            self._handle_execution_info(params)

    def clear(self, uri: str, version: int) -> None:
        # A session serializes calls for one URI, so starting a new version makes
        # all notification payloads and waiter events for older versions stale.
        for mapping in (
            self._diagnostics,
            self._diagnostic_events,
            self._file_progress,
            self._file_perf_data,
            self._perf_events,
            self._execution_info,
            self._execution_ranges,
            self._execution_events,
        ):
            for key in [key for key in mapping if key[0] == uri]:
                mapping.pop(key, None)
        self._latest_versions_by_uri.pop(uri, None)
        self._latest_progress_versions_by_uri.pop(uri, None)
        self._latest_perf_versions_by_uri.pop(uri, None)

    def _clear_stale_perf_data(self, uri: str, version: int | None) -> None:
        for key in list(self._file_perf_data):
            if key[0] == uri and key[1] != version:
                self._file_perf_data.pop(key, None)
                self._perf_events.pop(key, None)

    def _clear_stale_execution_data(self, uri: str, version: int | None) -> None:
        for key in list(self._execution_info):
            if key[0] == uri and key[1] != version:
                self._execution_info.pop(key, None)
                self._execution_ranges.pop(key, None)
                self._execution_events.pop(key, None)

    def latest_file_progress(self, uri: str, version: int) -> dict[str, Any] | None:
        key = (uri, version)
        if key in self._file_progress:
            return self._file_progress[key]

        latest = self._latest_progress_versions_by_uri.get(uri)
        if latest == version:
            return self._file_progress.get((uri, latest))
        return self._file_progress.get((uri, None))

    def latest_file_perf_data(self, uri: str, version: int) -> dict[str, Any] | None:
        key = (uri, version)
        if key in self._file_perf_data:
            return self._file_perf_data[key]

        latest = self._latest_perf_versions_by_uri.get(uri)
        if latest == version:
            return self._file_perf_data.get((uri, latest))
        return self._file_perf_data.get((uri, None))

    async def wait_for_file_perf_data(
        self, uri: str, version: int, settle_seconds: float = 0.25
    ) -> dict[str, Any] | None:
        key = (uri, version)
        if self.latest_file_perf_data(uri, version) is None:
            event = self._perf_events.setdefault(key, asyncio.Event())
            with suppress(TimeoutError):
                await asyncio.wait_for(event.wait(), timeout=settle_seconds)
        return self.latest_file_perf_data(uri, version)

    def latest_execution_sequence(self, uri: str, version: int) -> int:
        info = self._execution_info.get((uri, version))
        return info[0] if info is not None else 0

    def latest_execution_info(self, uri: str, version: int) -> dict[str, Any] | None:
        info = self._execution_info.get((uri, version))
        return info[1] if info is not None else None

    def execution_ranges(self, uri: str, version: int) -> list[dict[str, Any]]:
        ranges = self._execution_ranges.get((uri, version))
        if ranges is not None:
            return list(ranges)
        return list(self._execution_ranges.get((uri, None), []))

    async def wait_for_execution_info(
        self,
        uri: str,
        version: int,
        *,
        after_sequence: int,
        timeout: float | None,
    ) -> tuple[int, dict[str, Any]] | None:
        key = (uri, version)
        event = self._execution_events.setdefault(key, asyncio.Event())
        while True:
            event.clear()
            info = self._execution_info.get(key)
            if info is not None and info[0] > after_sequence:
                return info
            with suppress(TimeoutError):
                await asyncio.wait_for(event.wait(), timeout=timeout)
                continue
            return None

    def has_error_diagnostic(self, uri: str, version: int) -> bool:
        # Exact-version lookup only: stale errors from a previous document
        # version must not arm the after-error deadline.
        diagnostics = self._diagnostics.get((uri, version), [])
        return any(
            isinstance(diagnostic, dict) and diagnostic.get("severity") == 1
            for diagnostic in diagnostics
        )

    async def wait_for_error_diagnostic(self, uri: str, version: int) -> None:
        key = (uri, version)
        event = self._diagnostic_events.setdefault(key, asyncio.Event())
        while True:
            event.clear()
            if self.has_error_diagnostic(uri, version):
                return
            await event.wait()

    async def wait_for_diagnostics(
        self, uri: str, version: int, settle_seconds: float = 0.25
    ) -> list[dict[str, Any]]:
        key = (uri, version)
        if key not in self._diagnostics:
            event = self._diagnostic_events.setdefault(key, asyncio.Event())
            with suppress(TimeoutError):
                await asyncio.wait_for(event.wait(), timeout=settle_seconds)

        if key in self._diagnostics:
            return self._diagnostics[key]

        latest = self._latest_versions_by_uri.get(uri)
        if latest == version:
            return self._diagnostics.get((uri, latest), [])
        return self._diagnostics.get((uri, None), [])

    def _handle_publish_diagnostics(self, params: dict[str, Any]) -> None:
        uri = params.get("uri")
        version = params.get("version")
        diagnostics = params.get("diagnostics", [])
        if isinstance(uri, str) and isinstance(diagnostics, list):
            key = (uri, version)
            self._diagnostics[key] = diagnostics
            self._latest_versions_by_uri[uri] = version
            if event := self._diagnostic_events.get(key):
                event.set()

    def _handle_file_progress(self, params: dict[str, Any]) -> None:
        text_document = params.get("textDocument")
        if not isinstance(text_document, dict):
            return

        uri = text_document.get("uri")
        version = text_document.get("version")
        if not isinstance(uri, str):
            return

        self._file_progress[(uri, version)] = params
        self._latest_progress_versions_by_uri[uri] = version

    def _handle_file_perf_data(self, params: dict[str, Any]) -> None:
        text_document = params.get("textDocument")
        if not isinstance(text_document, dict):
            return

        uri = text_document.get("uri")
        version = text_document.get("version")
        timings = params.get("timings")
        if not isinstance(uri, str) or not isinstance(timings, list):
            return

        key = (uri, version)
        self._clear_stale_perf_data(uri, version)
        self._file_perf_data[key] = params
        self._latest_perf_versions_by_uri[uri] = version
        if event := self._perf_events.get(key):
            event.set()

    def _handle_execution_info(self, params: dict[str, Any]) -> None:
        text_document = params.get("textDocument")
        if not isinstance(text_document, dict):
            return

        uri = text_document.get("uri")
        version = text_document.get("version")
        range_ = params.get("range")
        if not isinstance(uri, str) or not isinstance(range_, dict):
            return

        self._execution_sequence += 1
        key = (uri, version)
        self._clear_stale_execution_data(uri, version)
        self._execution_info[key] = (self._execution_sequence, params)
        self._execution_ranges.setdefault(key, []).append(range_)
        if event := self._execution_events.get(key):
            event.set()
