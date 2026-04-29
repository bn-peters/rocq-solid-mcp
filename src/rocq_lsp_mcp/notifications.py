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

    def handle(self, method: str, params: Any | None) -> None:
        if method == "textDocument/publishDiagnostics" and isinstance(params, dict):
            self._handle_publish_diagnostics(params)
        if method == "$/coq/fileProgress" and isinstance(params, dict):
            self._handle_file_progress(params)

    def clear(self, uri: str, version: int) -> None:
        self._diagnostics.pop((uri, version), None)
        self._diagnostic_events.pop((uri, version), None)
        self._file_progress.pop((uri, version), None)
        if self._latest_progress_versions_by_uri.get(uri) == version:
            self._latest_progress_versions_by_uri.pop(uri, None)

    def latest_file_progress(self, uri: str, version: int) -> dict[str, Any] | None:
        key = (uri, version)
        if key in self._file_progress:
            return self._file_progress[key]

        latest = self._latest_progress_versions_by_uri.get(uri)
        if latest == version:
            return self._file_progress.get((uri, latest))
        return self._file_progress.get((uri, None))

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
