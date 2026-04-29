from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import RocqLspConfig, load_config
from .models import CompileResult
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

    async def close(self) -> None:
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
            raw_diagnostics = await session.notifications.wait_for_diagnostics(
                uri, version
            )
            diagnostics = build_diagnostics(raw_diagnostics, None)
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

    async def _get_or_create_session(self, workspace: Path) -> RocqLspSession:
        if session := self._sessions.get(workspace):
            return session

        session = RocqLspSession(workspace=workspace, config=self.config)
        await session.start()
        self._sessions[workspace] = session
        return session

    async def _reset_session(self, workspace: Path) -> None:
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
    def _range_key(range_: dict[str, Any]) -> str:
        return json.dumps(range_, sort_keys=True, separators=(",", ":"))
