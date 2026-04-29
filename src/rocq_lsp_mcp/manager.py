from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from .config import RocqLspConfig, load_config
from .models import CompileResult, location_string
from .pipeline import build_diagnostics
from .rocq_client import (
    RocqLspClient,
    RocqLspHardTimeout,
    RocqLspResponseError,
)
from .session import RocqLspSession


class RocqLspManager:
    def __init__(self, config: RocqLspConfig | None = None) -> None:
        self.config = config or load_config()
        self._sessions: dict[Path, RocqLspSession] = {}
        self._sessions_lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task[None] | None = None

    def start_idle_cleanup(self) -> None:
        if self.config.idle_ttl_seconds <= 0:
            return
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._idle_cleanup_loop())

    async def close(self) -> None:
        cleanup_task = self._cleanup_task
        self._cleanup_task = None
        if cleanup_task is not None:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task

        async with self._sessions_lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            await session.stop()

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
        session = await self._checkout_session(workspace)
        try:
            return await self._compile_with_session(session, path)
        finally:
            session.lock.release()
            await self._release_session(session)
            await self._cleanup_idle_sessions()

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
        session.notifications.clear(uri, version)

        try:
            if uri in session.versions:
                await client.notify_document_changed(path, content, version)
            else:
                await client.notify_document_opened(path, content, version)
            session.versions[uri] = version

            completed_doc, request_timed_out = await client.request_get_document(
                uri=uri,
                version=version,
                timeout=self.config.timeout_policy.request_seconds,
                hard_timeout_grace=self.config.timeout_policy.cancel_grace_seconds,
                goals="Str",
            )
            raw_diagnostics = await session.notifications.wait_for_diagnostics(
                uri, version
            )
            supplemental_proof_states = await self._incomplete_proof_goal_states(
                client, uri, version, raw_diagnostics
            )
            diagnostics = build_diagnostics(
                raw_diagnostics,
                completed_doc,
                supplemental_proof_states=supplemental_proof_states,
            )
            timeout_context = None
            if request_timed_out:
                timeout_context = self._timeout_context(
                    content=content,
                    document=completed_doc,
                    progress=session.notifications.latest_file_progress(uri, version),
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
                timeout_context=timeout_context,
            )
        except RocqLspHardTimeout as exc:
            raw_diagnostics = await session.notifications.wait_for_diagnostics(
                uri, version
            )
            diagnostics = build_diagnostics(raw_diagnostics, None)
            timeout_context = self._timeout_context(
                content=content,
                document=None,
                progress=session.notifications.latest_file_progress(uri, version),
            )
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
                timeout_context=timeout_context,
                message=str(exc),
            )
        except RocqLspResponseError as exc:
            raw_diagnostics = await session.notifications.wait_for_diagnostics(
                uri, version
            )
            diagnostics = build_diagnostics(raw_diagnostics, None)
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
            raw_diagnostics = await session.notifications.wait_for_diagnostics(
                uri, version
            )
            diagnostics = build_diagnostics(raw_diagnostics, None)
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

    async def _checkout_session(self, workspace: Path) -> RocqLspSession:
        while True:
            session = await self._reserve_session(workspace)
            try:
                await session.lock.acquire()
            except BaseException:
                await self._release_session(session)
                raise

            if await self._is_current_session(workspace, session):
                return session

            session.lock.release()
            await self._release_session(session)

    async def _reserve_session(self, workspace: Path) -> RocqLspSession:
        expired: list[RocqLspSession] = []
        session: RocqLspSession | None = None
        try:
            async with self._sessions_lock:
                expired = self._pop_idle_sessions_locked()
                session = self._sessions.get(workspace)
                if session is None:
                    session = await self._create_session(workspace)
                    self._sessions[workspace] = session
                session.active_requests += 1
        finally:
            for expired_session in expired:
                await expired_session.stop()

        if session is None:
            raise RuntimeError("failed to create rocq-lsp session")
        return session

    async def _is_current_session(
        self, workspace: Path, session: RocqLspSession
    ) -> bool:
        async with self._sessions_lock:
            return self._sessions.get(workspace) is session

    async def _create_session(self, workspace: Path) -> RocqLspSession:
        session = RocqLspSession(workspace=workspace, config=self.config)
        await session.start()
        return session

    async def _release_session(self, session: RocqLspSession) -> None:
        async with self._sessions_lock:
            if session.active_requests > 0:
                session.active_requests -= 1
            session.last_used_at = time.monotonic()

    async def _cleanup_idle_sessions(self) -> None:
        async with self._sessions_lock:
            expired = self._pop_idle_sessions_locked()
        for session in expired:
            await session.stop()

    async def _idle_cleanup_loop(self) -> None:
        interval = self._idle_cleanup_interval()
        while True:
            await asyncio.sleep(interval)
            await self._cleanup_idle_sessions()

    def _idle_cleanup_interval(self) -> float:
        ttl = self.config.idle_ttl_seconds
        if ttl <= 0:
            return 60.0
        return max(1.0, min(60.0, ttl / 2))

    def _pop_idle_sessions_locked(self) -> list[RocqLspSession]:
        ttl = self.config.idle_ttl_seconds
        if ttl <= 0:
            return []

        now = time.monotonic()
        expired: list[RocqLspSession] = []
        for workspace, session in list(self._sessions.items()):
            if session.active_requests > 0 or session.lock.locked():
                continue
            if now - session.last_used_at < ttl:
                continue
            expired.append(session)
            del self._sessions[workspace]
        return expired

    async def _reset_session(self, workspace: Path) -> None:
        async with self._sessions_lock:
            session = self._sessions.pop(workspace, None)
        if session is not None:
            await session.stop(hard=True)

    async def _incomplete_proof_goal_states(
        self,
        client: RocqLspClient,
        uri: str,
        version: int,
        raw_diagnostics: list[dict[str, Any]],
    ) -> dict[str, Any]:
        states: dict[str, Any] = {}
        requested: set[str] = set()
        for diagnostic in raw_diagnostics:
            if not self._needs_incomplete_proof_goal_request(diagnostic):
                continue
            range_ = self._diagnostic_sentence_range(diagnostic)
            position = self._range_start(range_)
            if position is None:
                continue
            key = self._range_key(range_)
            if key in requested:
                continue
            requested.add(key)
            try:
                states[key] = await client.request_goals(
                    uri=uri,
                    version=version,
                    position=position,
                    timeout=self.config.timeout_policy.request_seconds,
                )
            except Exception:
                continue
        return states

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

    def _timeout_context(
        self,
        *,
        content: str,
        document: dict[str, Any] | None,
        progress: dict[str, Any] | None,
    ) -> dict[str, Any]:
        completed = self._completed_only(document) if document else None
        completed_range = completed.get("range") if completed else None
        completed_end = (
            self._range_end(completed_range) if isinstance(completed_range, dict) else None
        )
        processing_range = self._latest_processing_range(progress)
        progress_start = (
            self._range_start(processing_range)
            if isinstance(processing_range, dict)
            else None
        )
        checked_until = completed_end or progress_start

        context: dict[str, Any] = {
            "operation": "coq/getDocument",
            "request_timeout_seconds": self.config.timeout_policy.request_seconds,
            "cancel_grace_seconds": self.config.timeout_policy.cancel_grace_seconds,
            "checked_until": self._position_string(checked_until),
            "latest_processing_range": location_string(processing_range),
            "next_unchecked": self._next_unchecked_preview(content, checked_until),
        }
        return context

    @staticmethod
    def _latest_processing_range(progress: Any) -> dict[str, Any] | None:
        if not isinstance(progress, dict):
            return None
        processing = progress.get("processing")
        if not isinstance(processing, list):
            return None
        for item in processing:
            if not isinstance(item, dict):
                continue
            range_ = item.get("range")
            if isinstance(range_, dict):
                return range_
        return None

    @staticmethod
    def _next_unchecked_preview(
        content: str, position: dict[str, int] | None
    ) -> dict[str, Any] | None:
        if position is None:
            return None

        line_number = position["line"]
        character = position["character"]
        lines = content.splitlines()
        for current_line_number in range(line_number, len(lines)):
            line = lines[current_line_number]
            start = character if current_line_number == line_number else 0
            start = max(0, min(start, len(line)))
            for index in range(start, len(line)):
                if not line[index].isspace():
                    return {
                        "location": f"{current_line_number + 1}:{index + 1}",
                        "line": RocqLspManager._truncate_preview_line(line.strip()),
                    }
        return None

    @staticmethod
    def _truncate_preview_line(line: str, limit: int = 160) -> str:
        if len(line) <= limit:
            return line
        return f"{line[: limit - 3]}..."

    @staticmethod
    def _needs_incomplete_proof_goal_request(diagnostic: Any) -> bool:
        if not isinstance(diagnostic, dict) or diagnostic.get("severity") != 1:
            return False
        message = diagnostic.get("message", "")
        return (
            isinstance(message, str)
            and "Attempt to save an incomplete proof" in message
        )

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
    def _range_start(range_: dict[str, Any]) -> dict[str, int] | None:
        start = range_.get("start")
        if not isinstance(start, dict):
            return None
        line = start.get("line")
        character = start.get("character")
        if isinstance(line, int) and isinstance(character, int):
            return {"line": line, "character": character}
        return None

    @staticmethod
    def _range_end(range_: dict[str, Any]) -> dict[str, int] | None:
        end = range_.get("end")
        if not isinstance(end, dict):
            return None
        line = end.get("line")
        character = end.get("character")
        if isinstance(line, int) and isinstance(character, int):
            return {"line": line, "character": character}
        return None

    @staticmethod
    def _position_string(position: dict[str, int] | None) -> str | None:
        if position is None:
            return None
        return f"{position['line'] + 1}:{position['character'] + 1}"

    @staticmethod
    def _range_key(range_: dict[str, Any]) -> str:
        return json.dumps(range_, sort_keys=True, separators=(",", ":"))
