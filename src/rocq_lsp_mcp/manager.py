from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import RocqLspConfig, load_config
from .models import CompileResult, Diagnostic
from .rocq_client import (
    RocqLspClient,
    RocqLspHardTimeout,
    RocqLspResponseError,
)


@dataclass
class RocqLspSession:
    workspace: Path
    client: RocqLspClient
    versions: dict[str, int] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class RocqLspManager:
    def __init__(self, config: RocqLspConfig | None = None) -> None:
        self.config = config or load_config()
        self._sessions: dict[Path, RocqLspSession] = {}

    async def close(self) -> None:
        sessions = list(self._sessions.values())
        self._sessions.clear()
        for session in sessions:
            await self._close_session(session)

    async def compile_file(self, file: str) -> CompileResult:
        path = self._resolve_file(file)
        if not path.is_file():
            return CompileResult.from_diagnostics(
                status="failed",
                file=str(path),
                uri=None,
                workspace=None,
                version=None,
                completed=None,
                diagnostics=[],
                message=f"File does not exist: {path}",
            )

        workspace = self._infer_workspace(path)
        session = await self._get_or_create_session(workspace)
        async with session.lock:
            return await self._compile_with_session(session, path)

    async def _compile_with_session(
        self, session: RocqLspSession, path: Path
    ) -> CompileResult:
        client = session.client
        uri = path.as_uri()
        workspace = str(session.workspace)
        version = session.versions.get(uri, 0) + 1
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            return CompileResult.from_diagnostics(
                status="failed",
                file=str(path),
                uri=uri,
                workspace=workspace,
                version=version,
                completed=None,
                diagnostics=[],
                message=str(exc),
            )
        client.clear_diagnostics(uri, version)

        try:
            if uri in session.versions:
                await client.notify_document_changed(path, content, version)
            else:
                await client.notify_document_opened(path, content, version)
            session.versions[uri] = version

            completed_doc, request_timed_out = await client.request_get_document(
                uri=uri,
                version=version,
                timeout=self.config.timeout,
                hard_timeout_grace=self.config.hard_timeout_grace,
                goals="Str",
            )
            diagnostics = await self._diagnostics_for(
                client, uri, version, document=completed_doc
            )
            return CompileResult.from_diagnostics(
                status="timeout" if request_timed_out else "complete",
                file=str(path),
                uri=uri,
                workspace=workspace,
                version=version,
                completed=self._completed_only(completed_doc),
                diagnostics=diagnostics,
                timed_out=request_timed_out,
            )
        except RocqLspHardTimeout as exc:
            diagnostics = await self._diagnostics_for(client, uri, version)
            await self._reset_session(session.workspace)
            return CompileResult.from_diagnostics(
                status="timeout",
                file=str(path),
                uri=uri,
                workspace=workspace,
                version=version,
                completed=None,
                diagnostics=diagnostics,
                timed_out=True,
                restarted=True,
                message=str(exc),
            )
        except RocqLspResponseError as exc:
            diagnostics = await self._diagnostics_for(client, uri, version)
            return CompileResult.from_diagnostics(
                status="failed",
                file=str(path),
                uri=uri,
                workspace=workspace,
                version=version,
                completed=None,
                diagnostics=diagnostics,
                message=str(exc.error),
            )
        except Exception as exc:
            diagnostics = await self._diagnostics_for(client, uri, version)
            return CompileResult.from_diagnostics(
                status="failed",
                file=str(path),
                uri=uri,
                workspace=workspace,
                version=version,
                completed=None,
                diagnostics=diagnostics,
                message=str(exc),
            )

    async def _diagnostics_for(
        self,
        client: RocqLspClient,
        uri: str,
        version: int,
        *,
        document: dict[str, Any] | None = None,
    ) -> list[Diagnostic]:
        raw = await client.wait_for_diagnostics(uri, version)
        proof_states = self._proof_states_by_range(document)
        return [
            Diagnostic.from_lsp(
                diagnostic,
                proof_state=proof_states.get(
                    self._range_key(self._diagnostic_sentence_range(diagnostic))
                ),
            )
            for diagnostic in raw
        ]

    async def _get_or_create_session(self, workspace: Path) -> RocqLspSession:
        if session := self._sessions.get(workspace):
            return session

        request_timeout = max(10.0, self.config.timeout + self.config.hard_timeout_grace + 5)
        client = RocqLspClient(
            binary=self.config.binary,
            workspace=workspace,
            max_errors=self.config.max_errors,
            request_timeout=request_timeout,
        )
        await client.start()
        await client.notify_change_configuration()
        session = RocqLspSession(workspace=workspace, client=client)
        self._sessions[workspace] = session
        return session

    async def _reset_session(self, workspace: Path) -> None:
        session = self._sessions.pop(workspace, None)
        if session is not None:
            await self._close_session(session, hard=True)

    async def _close_session(
        self, session: RocqLspSession, *, hard: bool = False
    ) -> None:
        if hard:
            with suppress(Exception):
                session.client.clear_pending_requests()
            with suppress(Exception):
                await session.client.kill_server()
        else:
            with suppress(Exception):
                await session.client.close()

    def _resolve_file(self, file: str) -> Path:
        path = Path(file).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return path.resolve()

    def _infer_workspace(self, file: Path) -> Path:
        if self.config.workspace is not None:
            return self.config.workspace

        for candidate in (file.parent, *file.parent.parents):
            if (candidate / "_RocqProject").exists() or (
                candidate / "_CoqProject"
            ).exists():
                return candidate.resolve()
        return file.parent.resolve()

    @staticmethod
    def _completed_only(document: dict[str, Any]) -> dict[str, Any] | None:
        completed = document.get("completed")
        return completed if isinstance(completed, dict) else None

    @staticmethod
    def _proof_states_by_range(document: dict[str, Any] | None) -> dict[str, Any]:
        if not document:
            return {}
        states: dict[str, Any] = {}
        for span in document.get("spans", []):
            if not isinstance(span, dict):
                continue
            goals = span.get("goals")
            range_ = span.get("range")
            if goals is not None and isinstance(range_, dict):
                states[RocqLspManager._range_key(range_)] = goals
        return states

    @staticmethod
    def _diagnostic_sentence_range(diagnostic: Any) -> dict[str, Any]:
        if not isinstance(diagnostic, dict):
            return {}
        data = diagnostic.get("data")
        if isinstance(data, dict) and isinstance(data.get("sentenceRange"), dict):
            return data["sentenceRange"]
        range_ = diagnostic.get("range")
        return range_ if isinstance(range_, dict) else {}

    @staticmethod
    def _range_key(range_: dict[str, Any]) -> str:
        return json.dumps(range_, sort_keys=True, separators=(",", ":"))
