from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .config import RocqLspConfig, bounded_overall_timeout_seconds, load_config
from .models import (
    CheckResult,
    DEFAULT_LONG_RUNNING_SECONDS,
    RunAtResult,
    TimeoutContext,
    long_running_commands_from_perf_data,
    run_at_location,
)
from .pipeline import build_diagnostics
from .rocq_client import (
    RocqLspClient,
    RocqLspHardTimeout,
    RocqLspResponseError,
    RocqLspUnavailableError,
)
from .session import RocqLspSession


@dataclass(frozen=True)
class DuneBuildResult:
    applied: bool = False
    message: str | None = None

    @property
    def failed(self) -> bool:
        return self.message is not None


@dataclass(frozen=True)
class DuneDetection:
    toplevel: Literal["rocq", "coq"] | None = None


def _dune_reported_build_actions(stderr: str) -> bool:
    """Whether dune's short-display stderr shows a real dependency rebuild.

    With ``--display=short`` dune reports build actions on stderr, but it also
    prints ``Entering directory``/``Leaving directory`` there for any invocation
    from a subdirectory of the workspace root, even when nothing was rebuilt.
    Reading that navigation noise as a rebuild makes every check restart the
    session and discard the incremental cache, so it is ignored here; any other
    non-empty line means a dependency was recompiled.
    """
    for line in stderr.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Entering directory ") or stripped.startswith(
            "Leaving directory "
        ):
            continue
        return True
    return False


class RocqLspManager:
    def __init__(self, config: RocqLspConfig | None = None) -> None:
        self.config = config or load_config()
        # Sessions are keyed by resolved Rocq file path. Each session still
        # starts coq-lsp with the inferred workspace as cwd/root.
        self._sessions: dict[Path, RocqLspSession] = {}
        self._sessions_lock = asyncio.Lock()
        self._dune_detection_cache: dict[Path, DuneDetection] = {}
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

    async def check_file(
        self,
        file: str,
        command_timeout_seconds: float | None = None,
        overall_timeout_seconds: float | None = None,
        long_running_seconds: float | None = DEFAULT_LONG_RUNNING_SECONDS,
        clear_cache: bool = False,
    ) -> CheckResult:
        command_timeout = self._effective_command_timeout(command_timeout_seconds)
        overall_timeout = self._effective_overall_timeout(overall_timeout_seconds)
        long_running_threshold = self._effective_long_running_threshold(
            long_running_seconds
        )
        path = self._resolve_file(file)
        if not path.is_file():
            return CheckResult.from_diagnostics(
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
        session_key = self._session_key(path)
        dune_build = await self._maybe_build_dune_dependencies(path)
        if dune_build.failed:
            return CheckResult.from_diagnostics(
                status="failed",
                file=str(path),
                uri=path.as_uri(),
                workspace=str(workspace),
                version=None,
                completed=None,
                diagnostics=[],
                message=dune_build.message,
            )
        try:
            session, created = await self._checkout_session(session_key, workspace)
        except RocqLspUnavailableError as exc:
            return CheckResult.from_diagnostics(
                status="failed",
                file=str(path),
                uri=path.as_uri(),
                workspace=str(workspace),
                version=None,
                completed=None,
                diagnostics=[],
                message=str(exc),
            )
        try:
            should_restart = clear_cache or dune_build.applied
            restarted = False
            if should_restart and not created:
                session = await self._restart_checked_out_session(
                    session_key,
                    workspace,
                    session,
                )
                restarted = True
            result = await self._check_with_session(
                session,
                session_key,
                path,
                command_timeout,
                overall_timeout,
                long_running_threshold,
            )
            if restarted:
                result = result.model_copy(update={"restarted": True})
            return result
        finally:
            session.lock.release()
            await self._release_session(session)
            await self._cleanup_idle_sessions()

    async def run_at(
        self,
        file: str,
        commands: str,
        line: int,
        character: int = 1,
        overall_timeout_seconds: float | None = None,
    ) -> RunAtResult:
        overall_timeout = self._effective_overall_timeout(overall_timeout_seconds)
        path = self._resolve_file(file)
        stripped_commands = commands.strip()
        location = (
            run_at_location(line, character)
            if isinstance(line, int)
            and isinstance(character, int)
            and line > 0
            and character > 0
            else None
        )
        if not path.is_file():
            return RunAtResult.from_feedback(
                status="failed",
                file=str(path),
                commands=stripped_commands,
                location=location,
                uri=None,
                workspace=None,
                version=None,
                feedback=[],
                message=f"File does not exist: {path}",
            )

        workspace = self._infer_workspace(path)
        session_key = self._session_key(path)
        validation_error = self._run_at_validation_error(
            stripped_commands, line, character
        )
        if validation_error is not None:
            return RunAtResult.from_feedback(
                status="failed",
                file=str(path),
                commands=stripped_commands,
                location=location,
                uri=None,
                workspace=str(workspace),
                version=None,
                feedback=[],
                message=validation_error,
            )

        dune_build = await self._maybe_build_dune_dependencies(path)
        if dune_build.failed:
            return RunAtResult.from_feedback(
                status="failed",
                file=str(path),
                commands=stripped_commands,
                location=location,
                uri=path.as_uri(),
                workspace=str(workspace),
                version=None,
                feedback=[],
                message=dune_build.message,
            )

        try:
            session, created = await self._checkout_session(session_key, workspace)
        except RocqLspUnavailableError as exc:
            return RunAtResult.from_feedback(
                status="failed",
                file=str(path),
                commands=stripped_commands,
                location=location,
                uri=path.as_uri(),
                workspace=str(workspace),
                version=None,
                feedback=[],
                message=str(exc),
            )
        try:
            restarted = False
            if dune_build.applied and not created:
                session = await self._restart_checked_out_session(
                    session_key,
                    workspace,
                    session,
                )
                restarted = True
            result = await self._run_at_with_session(
                session,
                session_key,
                path,
                stripped_commands,
                line,
                character,
                overall_timeout,
            )
            if restarted:
                result = result.model_copy(update={"restarted": True})
            return result
        finally:
            session.lock.release()
            await self._release_session(session)
            await self._cleanup_idle_sessions()

    async def _check_with_session(
        self,
        session: RocqLspSession,
        session_key: Path,
        path: Path,
        command_timeout: float | None,
        overall_timeout: float | None,
        long_running_threshold: float | None,
    ) -> CheckResult:
        client = session.client
        uri = path.as_uri()
        workspace = str(session.workspace)
        version = session.versions.get(uri, 0) + 1
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            return CheckResult.from_diagnostics(
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

            get_document_result = await client.request_get_document(
                uri=uri,
                version=version,
                timeout=overall_timeout,
                hard_timeout_grace=self.config.timeout_policy.cancel_grace_seconds,
                command_timeout=command_timeout,
                goals="Str",
            )
            completed_doc = get_document_result.document
            request_timed_out = get_document_result.timed_out
            raw_diagnostics, perf_data = await self._check_notifications(
                session, uri, version, long_running_threshold, request_timed_out
            )
            supplemental_proof_states = await self._incomplete_proof_goal_states(
                client, uri, version, raw_diagnostics, overall_timeout
            )
            diagnostics = build_diagnostics(
                raw_diagnostics,
                completed_doc,
                content=content,
                supplemental_proof_states=supplemental_proof_states,
            )
            long_running_commands = (
                long_running_commands_from_perf_data(
                    content=content,
                    perf_data=perf_data,
                    threshold_seconds=long_running_threshold,
                    executed_ranges=session.notifications.execution_ranges(
                        uri, version
                    ),
                )
                if long_running_threshold is not None
                else []
            )
            timeout_context = None
            if request_timed_out:
                timeout_context = TimeoutContext.from_manager_state(
                    content=content,
                    document=completed_doc,
                    progress=session.notifications.latest_file_progress(uri, version),
                    cancel_grace_seconds=(
                        self.config.timeout_policy.cancel_grace_seconds
                    ),
                    timeout_kind=get_document_result.timeout_kind or "request",
                    request_timeout_seconds=overall_timeout,
                    command_timeout_seconds=(
                        command_timeout
                        if get_document_result.timeout_kind == "command"
                        else None
                    ),
                    timed_out_range=get_document_result.timed_out_range,
                )
            return CheckResult.from_diagnostics(
                status="timeout" if request_timed_out else "complete",
                file=str(path),
                uri=uri,
                workspace=workspace,
                version=version,
                completed=self._completed_only(completed_doc),
                diagnostics=diagnostics,
                timed_out=request_timed_out,
                timeout_context=timeout_context,
                long_running_commands=long_running_commands,
            )
        except asyncio.CancelledError:
            if getattr(client, "restart_required_after_external_cancellation", False):
                await self._reset_session(session_key)
            raise
        except RocqLspHardTimeout as exc:
            raw_diagnostics = await session.notifications.wait_for_diagnostics(
                uri, version
            )
            diagnostics = build_diagnostics(raw_diagnostics, None, content=content)
            timeout_context = TimeoutContext.from_manager_state(
                content=content,
                document=None,
                progress=session.notifications.latest_file_progress(uri, version),
                cancel_grace_seconds=self.config.timeout_policy.cancel_grace_seconds,
                timeout_kind=exc.timeout_kind,
                request_timeout_seconds=overall_timeout,
                command_timeout_seconds=(
                    command_timeout if exc.timeout_kind == "command" else None
                ),
                timed_out_range=exc.timed_out_range,
            )
            await self._reset_session(session_key)
            return CheckResult.from_diagnostics(
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
            diagnostics = build_diagnostics(raw_diagnostics, None, content=content)
            return CheckResult.from_diagnostics(
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
            diagnostics = build_diagnostics(raw_diagnostics, None, content=content)
            return CheckResult.from_diagnostics(
                status="failed",
                file=str(path),
                uri=uri,
                workspace=workspace,
                version=version,
                completed=None,
                diagnostics=diagnostics,
                message=str(exc),
            )

    async def _run_at_with_session(
        self,
        session: RocqLspSession,
        session_key: Path,
        path: Path,
        commands: str,
        line: int,
        character: int,
        overall_timeout: float | None,
    ) -> RunAtResult:
        client = session.client
        uri = path.as_uri()
        workspace = str(session.workspace)
        version = session.versions.get(uri, 0) + 1
        location = run_at_location(line, character)
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            return RunAtResult.from_feedback(
                status="failed",
                file=str(path),
                commands=commands,
                location=location,
                uri=uri,
                workspace=workspace,
                version=version,
                feedback=[],
                message=str(exc),
            )
        session.notifications.clear(uri, version)

        try:
            if uri in session.versions:
                await client.notify_document_changed(path, content, version)
            else:
                await client.notify_document_opened(path, content, version)
            session.versions[uri] = version

            result, request_timed_out = await client.request_run_at_point(
                uri=uri,
                version=version,
                position={"line": line - 1, "character": character - 1},
                commands=commands,
                timeout=overall_timeout,
                hard_timeout_grace=self.config.timeout_policy.cancel_grace_seconds,
            )
            return RunAtResult.from_feedback(
                status="timeout" if request_timed_out else "complete",
                file=str(path),
                commands=commands,
                location=location,
                uri=uri,
                workspace=workspace,
                version=version,
                feedback=result,
                timed_out=request_timed_out,
            )
        except asyncio.CancelledError:
            if getattr(client, "restart_required_after_external_cancellation", False):
                await self._reset_session(session_key)
            raise
        except RocqLspHardTimeout as exc:
            await self._reset_session(session_key)
            return RunAtResult.from_feedback(
                status="timeout",
                file=str(path),
                commands=commands,
                location=location,
                uri=uri,
                workspace=workspace,
                version=version,
                feedback=[],
                timed_out=True,
                restarted=True,
                message=str(exc),
            )
        except RocqLspResponseError as exc:
            return RunAtResult.from_feedback(
                status="failed",
                file=str(path),
                commands=commands,
                location=location,
                uri=uri,
                workspace=workspace,
                version=version,
                feedback=self._response_error_feedback(exc.error),
                message=self._response_error_message(exc.error),
            )
        except Exception as exc:
            return RunAtResult.from_feedback(
                status="failed",
                file=str(path),
                commands=commands,
                location=location,
                uri=uri,
                workspace=workspace,
                version=version,
                feedback=[],
                message=str(exc),
            )

    async def _checkout_session(
        self,
        session_key: Path,
        workspace: Path,
    ) -> tuple[RocqLspSession, bool]:
        while True:
            session, created = await self._reserve_session(session_key, workspace)
            try:
                await session.lock.acquire()
            except BaseException:
                await self._release_session(session)
                raise

            if await self._is_current_session(session_key, session):
                if session.workspace == workspace:
                    return session, created
                session.lock.release()
                await self._release_session(session)
                await self._reset_session(session_key)
                continue

            session.lock.release()
            await self._release_session(session)

    async def _restart_checked_out_session(
        self,
        session_key: Path,
        workspace: Path,
        session: RocqLspSession,
    ) -> RocqLspSession:
        replacement = await self._create_session(workspace)
        replacement.active_requests = 1
        await replacement.lock.acquire()

        replaced = False
        async with self._sessions_lock:
            if self._sessions.get(session_key) is session:
                self._sessions[session_key] = replacement
                replaced = True

        if not replaced:
            replacement.lock.release()
            await self._release_session(replacement)
            await replacement.stop(hard=True)
            session.lock.release()
            await self._release_session(session)
            checked_out, _created = await self._checkout_session(session_key, workspace)
            return checked_out

        session.lock.release()
        await self._release_session(session)
        await session.stop(hard=True)
        return replacement

    def _effective_command_timeout(self, override: float | None) -> float | None:
        if override is None:
            return self.config.timeout_policy.command_seconds
        if override <= 0:
            return None
        return override

    def _effective_overall_timeout(self, override: float | None) -> float | None:
        if override is None:
            return bounded_overall_timeout_seconds(
                self.config.timeout_policy.request_seconds
            )
        if override <= 0:
            return None
        return bounded_overall_timeout_seconds(override)

    def _effective_long_running_threshold(self, override: float | None) -> float | None:
        if override is None:
            return DEFAULT_LONG_RUNNING_SECONDS
        if override <= 0:
            return None
        return override

    async def _check_notifications(
        self,
        session: RocqLspSession,
        uri: str,
        version: int,
        long_running_threshold: float | None,
        request_timed_out: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        diagnostics_task = asyncio.create_task(
            session.notifications.wait_for_diagnostics(uri, version)
        )
        perf_task: asyncio.Task[dict[str, Any] | None] | None = None
        if long_running_threshold is not None and not request_timed_out:
            perf_task = asyncio.create_task(
                session.notifications.wait_for_file_perf_data(uri, version)
            )

        try:
            raw_diagnostics = await diagnostics_task
            perf_data = await perf_task if perf_task is not None else None
            return raw_diagnostics, perf_data
        finally:
            if perf_task is not None and not perf_task.done():
                perf_task.cancel()
                with suppress(asyncio.CancelledError):
                    await perf_task
            if not diagnostics_task.done():
                diagnostics_task.cancel()
                with suppress(asyncio.CancelledError):
                    await diagnostics_task

    async def _reserve_session(
        self,
        session_key: Path,
        workspace: Path,
    ) -> tuple[RocqLspSession, bool]:
        expired: list[RocqLspSession] = []
        session: RocqLspSession | None = None
        created = False
        try:
            async with self._sessions_lock:
                expired = self._pop_idle_sessions_locked()
                session = self._sessions.get(session_key)
                if session is None:
                    session = await self._create_session(workspace)
                    self._sessions[session_key] = session
                    created = True
                session.active_requests += 1
        finally:
            for expired_session in expired:
                await expired_session.stop()

        if session is None:
            raise RuntimeError("failed to create rocq-lsp session")
        return session, created

    async def _is_current_session(
        self, session_key: Path, session: RocqLspSession
    ) -> bool:
        async with self._sessions_lock:
            return self._sessions.get(session_key) is session

    async def _create_session(self, workspace: Path) -> RocqLspSession:
        session = RocqLspSession(workspace=workspace, config=self.config)
        await session.start()
        return session

    async def _maybe_build_dune_dependencies(self, path: Path) -> DuneBuildResult:
        if not self.config.dune_build:
            return DuneBuildResult()
        detection = await self._detect_dune(path)
        if detection.toplevel is None:
            return DuneBuildResult()

        command = (
            "dune",
            detection.toplevel,
            "top",
            path.name,
            "--toplevel",
            "echo",
            "--display=short",
        )
        result = await self._run_dune_command(path.parent, command)
        if result is None:
            return DuneBuildResult()
        returncode, stdout, stderr = result
        if returncode == 0:
            # The echo toplevel always prints arguments to stdout. With
            # --display=short, dune reports actual build actions on stderr,
            # alongside "Entering directory"/"Leaving directory" navigation
            # noise that must not be mistaken for a rebuild.
            return DuneBuildResult(applied=_dune_reported_build_actions(stderr))
        output = "\n".join(part for part in (stderr.strip(), stdout.strip()) if part)
        return DuneBuildResult(
            message=output or f"dune dependency build failed for {path.name}"
        )

    async def _detect_dune(self, path: Path) -> DuneDetection:
        cached = self._dune_detection_cache.get(path)
        if cached is not None:
            return cached

        if not await self._dune_command_succeeds(
            path.parent,
            ("dune", "describe", "workspace"),
        ):
            detection = DuneDetection()
            self._dune_detection_cache[path] = detection
            return detection

        toplevel = await self._dune_toplevel(path)
        detection = DuneDetection(toplevel=toplevel)
        self._dune_detection_cache[path] = detection
        return detection

    async def _dune_toplevel(self, path: Path) -> str | None:
        for toplevel in ("rocq", "coq"):
            command = (
                "dune",
                toplevel,
                "top",
                "--no-build",
                "--toplevel",
                "echo",
                path.name,
            )
            if await self._dune_command_succeeds(path.parent, command):
                return toplevel
        return None

    async def _dune_command_succeeds(
        self,
        cwd: Path,
        command: tuple[str, ...],
    ) -> bool:
        result = await self._run_dune_command(cwd, command)
        return result is not None and result[0] == 0

    async def _run_dune_command(
        self,
        cwd: Path,
        command: tuple[str, ...],
    ) -> tuple[int, str, str] | None:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return None

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.config.dune_timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            with suppress(ProcessLookupError):
                await process.wait()
            return (
                1,
                "",
                f"dune dependency build timed out after "
                f"{self.config.dune_timeout_seconds:g} seconds",
            )
        return (
            process.returncode or 0,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )

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
        for session_key, session in list(self._sessions.items()):
            if session.active_requests > 0 or session.lock.locked():
                continue
            if now - session.last_used_at < ttl:
                continue
            expired.append(session)
            del self._sessions[session_key]
        return expired

    async def _reset_session(self, session_key: Path) -> None:
        async with self._sessions_lock:
            session = self._sessions.pop(session_key, None)
        if session is not None:
            await session.stop(hard=True)

    async def _incomplete_proof_goal_states(
        self,
        client: RocqLspClient,
        uri: str,
        version: int,
        raw_diagnostics: list[dict[str, Any]],
        overall_timeout: float | None,
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
                    timeout=overall_timeout,
                )
            except Exception:
                continue
        return states

    def _resolve_file(self, file: str) -> Path:
        path = Path(file).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return path.resolve()

    @staticmethod
    def _session_key(file: Path) -> Path:
        return file.resolve()

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

    @staticmethod
    def _run_at_validation_error(
        commands: str, line: int, character: int
    ) -> str | None:
        if not isinstance(line, int) or not isinstance(character, int):
            return "line and character must be positive 1-based integers"
        if line <= 0 or character <= 0:
            return "line and character must be positive 1-based integers"
        if not commands:
            return "commands must be a non-empty Rocq command string ending in '.'"
        if not commands.endswith("."):
            return "commands must be a non-empty Rocq command string ending in '.'"
        return None

    @staticmethod
    def _response_error_message(error: dict[str, Any]) -> str:
        message = error.get("message") if isinstance(error, dict) else None
        return message if isinstance(message, str) else str(error)

    @staticmethod
    def _response_error_feedback(error: dict[str, Any]) -> Any:
        if not isinstance(error, dict):
            return []
        data = error.get("data")
        if isinstance(data, dict):
            feedback = data.get("feedback")
            return feedback if isinstance(feedback, list) else []
        return []
