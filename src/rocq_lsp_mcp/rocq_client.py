from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .config import rocq_lsp_command, rocq_lsp_settings
from .notifications import NotificationStore


TimeoutKind = Literal["request", "command", "after_error"]


class RocqLspResponseError(RuntimeError):
    def __init__(self, error: dict[str, Any]) -> None:
        self.error = error
        super().__init__(str(error))


class RocqLspHardTimeout(TimeoutError):
    def __init__(
        self,
        message: str,
        *,
        timeout_kind: TimeoutKind = "request",
        timed_out_range: dict[str, Any] | None = None,
    ) -> None:
        self.timeout_kind = timeout_kind
        self.timed_out_range = timed_out_range
        super().__init__(message)


class RocqLspProcessExited(RuntimeError):
    pass


class RocqLspUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class GetDocumentResult:
    document: dict[str, Any]
    timed_out: bool = False
    timeout_kind: TimeoutKind | None = None
    timed_out_range: dict[str, Any] | None = None


def language_id_for_path(path: Path) -> str:
    name = path.name
    if name.endswith(".mv"):
        return "markdown"
    if name.endswith(".lv") or name.endswith(".v.tex"):
        return "latex"
    return "rocq"


def _range_reaches_end_of_content(
    content: str | None, range_: dict[str, Any] | None
) -> bool:
    if content is None or range_ is None:
        return False

    end = range_.get("end")
    if not isinstance(end, dict):
        return False

    line = end.get("line")
    character = end.get("character")
    if not isinstance(line, int) or not isinstance(character, int):
        return False

    lines = content.splitlines(keepends=True)
    if line < 0 or line >= len(lines):
        return False

    suffix = "".join([lines[line][character:], *lines[line + 1 :]])
    return suffix.strip() == ""


class RocqLspClient:
    def __init__(
        self,
        *,
        binary: str,
        use_opam: bool,
        workspace: Path,
        max_errors: int,
        request_timeout: float,
        notifications: NotificationStore,
    ) -> None:
        self.command = rocq_lsp_command(binary, use_opam=use_opam)
        self.workspace = workspace
        self.max_errors = max_errors
        self.request_timeout = request_timeout
        self.notifications = notifications
        self.process: asyncio.subprocess.Process | None = None
        self._next_request_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self.stderr_lines: list[str] = []
        self.restart_required_after_external_cancellation = False

    async def start(self) -> None:
        self._ensure_launcher_available()
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.command,
                cwd=self.workspace,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._reader_task = asyncio.create_task(self._reader_loop())
            self._stderr_task = asyncio.create_task(self._drain_stderr())

            await self.request(
                "initialize",
                {
                    "processId": os.getpid(),
                    "clientInfo": {"name": "bens-rocq-mcp"},
                    "rootPath": str(self.workspace),
                    "rootUri": self.workspace.as_uri(),
                    "workspaceFolders": [
                        {"uri": self.workspace.as_uri(), "name": self.workspace.name}
                    ],
                    "capabilities": {
                        "textDocument": {
                            "publishDiagnostics": {
                                "relatedInformation": True,
                                "dataSupport": True,
                            }
                        },
                        "workspace": {"workspaceFolders": True},
                    },
                    "initializationOptions": rocq_lsp_settings(self.max_errors),
                    "trace": "off",
                },
                timeout=self.request_timeout,
            )
            await self.notify("initialized", {})
        except asyncio.CancelledError:
            raise
        except FileNotFoundError as exc:
            program = str(exc.filename or self.command[0])
            raise RocqLspUnavailableError(
                self._command_not_found_message(program)
            ) from exc
        except Exception as exc:
            raise RocqLspUnavailableError(
                await self._startup_failure_message(exc)
            ) from exc

    async def close(self, *, hard: bool = False) -> None:
        if hard:
            await self.kill_server()
            return

        with suppress(Exception):
            await self.request("shutdown", None, timeout=2)
            await self.notify("exit", None)
        await self.kill_server()

    async def kill_server(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()

        process = self.process
        if process is None:
            return
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except TimeoutError:
                process.kill()
                await process.wait()

    def clear_pending_requests(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

    async def notify_change_configuration(self) -> None:
        await self.notify(
            "workspace/didChangeConfiguration",
            {"settings": rocq_lsp_settings(self.max_errors)},
        )

    async def notify_document_opened(
        self, file_path: Path, content: str, version: int
    ) -> None:
        await self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": file_path.as_uri(),
                    "languageId": language_id_for_path(file_path),
                    "version": version,
                    "text": content,
                }
            },
        )

    async def notify_document_changed(
        self, file_path: Path, content: str, version: int
    ) -> None:
        await self.notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": file_path.as_uri(), "version": version},
                "contentChanges": [{"text": content}],
            },
        )

    async def request_get_document(
        self,
        *,
        uri: str,
        version: int,
        timeout: float | None,
        hard_timeout_grace: float,
        command_timeout: float | None = None,
        timeout_after_error: float | None = None,
        goals: str | None = None,
        content: str | None = None,
    ) -> GetDocumentResult:
        if command_timeout is not None and command_timeout <= 0:
            command_timeout = None
        if timeout_after_error is not None and timeout_after_error <= 0:
            timeout_after_error = None
        request_id = self._reserve_request()
        future = self._pending[request_id]
        self.restart_required_after_external_cancellation = False
        command_state: dict[str, Any] = {
            "timed_out": False,
            "range": None,
            "timeout_kind": None,
        }
        watchdog: asyncio.Task[None] | None = None
        params: dict[str, Any] = {"textDocument": {"uri": uri, "version": version}}
        if goals is not None:
            params["goals"] = goals
        await self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "coq/getDocument",
                "params": params,
            }
        )
        if command_timeout is not None or timeout_after_error is not None:
            watchdog = asyncio.create_task(
                self._command_timeout_watchdog(
                    uri=uri,
                    version=version,
                    request_id=request_id,
                    command_timeout=command_timeout,
                    timeout_after_error=timeout_after_error,
                    command_state=command_state,
                    content=content,
                )
            )

        try:
            if watchdog is None:
                try:
                    raw = await asyncio.wait_for(
                        asyncio.shield(future), timeout=timeout
                    )
                except TimeoutError:
                    await self.notify("$/cancelRequest", {"id": request_id})
                    return await self._wait_for_cancelled_get_document(
                        request_id=request_id,
                        future=future,
                        hard_timeout_grace=hard_timeout_grace,
                        timeout_kind="request",
                        timeout_seconds=timeout,
                        timed_out_range=None,
                    )
                self._pending.pop(request_id, None)
                return GetDocumentResult(document=self._result_or_raise(raw))

            done, _ = await asyncio.wait(
                {future, watchdog},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                await self.notify("$/cancelRequest", {"id": request_id})
                return await self._wait_for_cancelled_get_document(
                    request_id=request_id,
                    future=future,
                    hard_timeout_grace=hard_timeout_grace,
                    timeout_kind="request",
                    timeout_seconds=timeout,
                    timed_out_range=None,
                )

            if future in done or future.done():
                raw = future.result()
                self._pending.pop(request_id, None)
                if command_state["timed_out"] and "error" in raw:
                    return self._get_document_timeout_result(raw, command_state)
                return GetDocumentResult(document=self._result_or_raise(raw))

            await watchdog
            if future.done():
                raw = future.result()
                self._pending.pop(request_id, None)
                if "error" in raw:
                    return self._get_document_timeout_result(raw, command_state)
                return GetDocumentResult(document=self._result_or_raise(raw))
            timeout_kind, timeout_seconds = self._command_timeout_details(
                command_state, command_timeout, timeout_after_error
            )
            return await self._wait_for_cancelled_get_document(
                request_id=request_id,
                future=future,
                hard_timeout_grace=hard_timeout_grace,
                timeout_kind=timeout_kind,
                timeout_seconds=timeout_seconds,
                timed_out_range=command_state["range"],
            )

        except asyncio.CancelledError:
            await self._cancel_lsp_request_after_external_cancellation(
                request_id=request_id,
                future=future,
                hard_timeout_grace=hard_timeout_grace,
            )
            raise
        finally:
            if watchdog is not None:
                watchdog.cancel()
                with suppress(asyncio.CancelledError):
                    await watchdog

    async def _command_timeout_watchdog(
        self,
        *,
        uri: str,
        version: int,
        request_id: int,
        command_timeout: float | None,
        timeout_after_error: float | None,
        command_state: dict[str, Any],
        content: str | None,
    ) -> None:
        notifications = self.notifications
        loop = asyncio.get_running_loop()
        after_sequence = notifications.latest_execution_sequence(uri, version)
        active_range: dict[str, Any] | None = None
        watch_errors = timeout_after_error is not None
        # Absolute deadline for the whole call, armed when the first error
        # diagnostic is observed. Unlike the per-sentence deadline it never
        # resets, and it also covers the final sentence and end-of-file
        # finalization, which the per-sentence watchdog exempts: after an
        # error a bounded exit matters more than attributing that time
        # precisely.
        deadline: float | None = None
        exec_task: asyncio.Task[tuple[int, dict[str, Any]] | None] | None = None
        error_task: asyncio.Task[None] | None = None

        async def cancel_request(
            timed_out_range: dict[str, Any] | None, *, timeout_kind: TimeoutKind
        ) -> None:
            command_state["timed_out"] = True
            command_state["range"] = timed_out_range
            command_state["timeout_kind"] = timeout_kind
            await self.notify("$/cancelRequest", {"id": request_id})

        try:
            while True:
                if watch_errors and deadline is None:
                    if notifications.has_error_diagnostic(uri, version):
                        deadline = loop.time() + (timeout_after_error or 0)

                remaining = None if deadline is None else deadline - loop.time()
                if remaining is not None and remaining <= 0:
                    await cancel_request(active_range, timeout_kind="after_error")
                    return

                is_final_sentence = _range_reaches_end_of_content(
                    content, active_range
                )
                per_sentence = (
                    command_timeout
                    if active_range is not None and not is_final_sentence
                    else None
                )
                candidates = [
                    value for value in (per_sentence, remaining) if value is not None
                ]
                wait_timeout = min(candidates) if candidates else None

                if exec_task is None or exec_task.done():
                    exec_task = asyncio.create_task(
                        notifications.wait_for_execution_info(
                            uri,
                            version,
                            after_sequence=after_sequence,
                            timeout=None,
                        )
                    )
                waiters: set[asyncio.Task[Any]] = {exec_task}
                if watch_errors and deadline is None:
                    if error_task is None or error_task.done():
                        error_task = asyncio.create_task(
                            notifications.wait_for_error_diagnostic(uri, version)
                        )
                    waiters.add(error_task)

                done, _ = await asyncio.wait(
                    waiters,
                    timeout=wait_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    if deadline is not None and loop.time() >= deadline:
                        await cancel_request(active_range, timeout_kind="after_error")
                    else:
                        await cancel_request(active_range, timeout_kind="command")
                    return
                if exec_task in done:
                    update = exec_task.result()
                    if update is not None:
                        after_sequence, params = update
                        range_ = (
                            params.get("range") if isinstance(params, dict) else None
                        )
                        active_range = range_ if isinstance(range_, dict) else None
                if error_task is not None and error_task in done and deadline is None:
                    deadline = loop.time() + (timeout_after_error or 0)
        finally:
            for task in (exec_task, error_task):
                if task is not None and not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

    @staticmethod
    def _command_timeout_details(
        command_state: dict[str, Any],
        command_timeout: float | None,
        timeout_after_error: float | None,
    ) -> tuple[TimeoutKind, float]:
        kind: TimeoutKind = command_state.get("timeout_kind") or "command"
        if kind == "after_error":
            return kind, timeout_after_error or 0
        return kind, command_timeout or 0

    def _get_document_timeout_result(
        self, raw: dict[str, Any], command_state: dict[str, Any]
    ) -> GetDocumentResult:
        document = {} if "error" in raw else self._result_or_raise(raw)
        return GetDocumentResult(
            document=document,
            timed_out=True,
            timeout_kind=command_state.get("timeout_kind") or "command",
            timed_out_range=command_state["range"],
        )

    async def _wait_for_cancelled_get_document(
        self,
        *,
        request_id: int,
        future: asyncio.Future[dict[str, Any]],
        hard_timeout_grace: float,
        timeout_kind: TimeoutKind,
        timeout_seconds: float,
        timed_out_range: dict[str, Any] | None,
    ) -> GetDocumentResult:
        try:
            raw = await asyncio.wait_for(
                asyncio.shield(future), timeout=hard_timeout_grace
            )
        except asyncio.CancelledError:
            await self._cancel_lsp_request_after_external_cancellation(
                request_id=request_id,
                future=future,
                hard_timeout_grace=hard_timeout_grace,
            )
            raise
        except TimeoutError as exc:
            self._pending.pop(request_id, None)
            future.cancel()
            raise RocqLspHardTimeout(
                f"coq/getDocument {timeout_kind} timed out after "
                f"{timeout_seconds}s and did not settle within "
                f"{hard_timeout_grace}s",
                timeout_kind=timeout_kind,
                timed_out_range=timed_out_range,
            ) from exc
        finally:
            self._pending.pop(request_id, None)

        document = {} if "error" in raw else self._result_or_raise(raw)
        return GetDocumentResult(
            document=document,
            timed_out=True,
            timeout_kind=timeout_kind,
            timed_out_range=timed_out_range,
        )

    async def request_goals(
        self,
        *,
        uri: str,
        version: int,
        position: dict[str, int],
        timeout: float | None = None,
        pp_format: str = "Str",
        compact: bool = True,
        mode: str = "Prev",
    ) -> dict[str, Any]:
        return await self.request(
            "proof/goals",
            {
                "textDocument": {"uri": uri, "version": version},
                "position": position,
                "pp_format": pp_format,
                "compact": compact,
                "mode": mode,
            },
            timeout=timeout,
        )

    async def request_run_at_point(
        self,
        *,
        uri: str,
        version: int,
        position: dict[str, int],
        commands: str,
        timeout: float | None,
        hard_timeout_grace: float,
    ) -> tuple[dict[str, Any], bool]:
        request_id = self._reserve_request()
        future = self._pending[request_id]
        self.restart_required_after_external_cancellation = False
        await self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "petanque/run_at_point",
                "params": {
                    "textDocument": {"uri": uri, "version": version},
                    "position": position,
                    "command": commands,
                },
            }
        )

        try:
            raw = await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
            self._pending.pop(request_id, None)
            return self._result_or_raise(raw), False
        except TimeoutError as exc:
            await self.notify("$/cancelRequest", {"id": request_id})
            try:
                raw = await asyncio.wait_for(
                    asyncio.shield(future), timeout=hard_timeout_grace
                )
            except asyncio.CancelledError:
                await self._cancel_lsp_request_after_external_cancellation(
                    request_id=request_id,
                    future=future,
                    hard_timeout_grace=hard_timeout_grace,
                )
                raise
            except TimeoutError:
                self._pending.pop(request_id, None)
                future.cancel()
                raise RocqLspHardTimeout(
                    f"petanque/run_at_point timed out after {timeout}s and did not "
                    f"settle within {hard_timeout_grace}s"
                ) from exc
            finally:
                self._pending.pop(request_id, None)

            if "error" in raw:
                return {}, True
            return self._result_or_raise(raw), True
        except asyncio.CancelledError:
            await self._cancel_lsp_request_after_external_cancellation(
                request_id=request_id,
                future=future,
                hard_timeout_grace=hard_timeout_grace,
            )
            raise

    async def request(
        self, method: str, params: Any | None, *, timeout: float | None = None
    ) -> dict[str, Any]:
        request_id = self._reserve_request()
        future = self._pending[request_id]
        await self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        try:
            raw = await asyncio.wait_for(
                asyncio.shield(future), timeout=timeout or self.request_timeout
            )
            return self._result_or_raise(raw)
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: Any | None = None) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _cancel_lsp_request_after_external_cancellation(
        self,
        *,
        request_id: int,
        future: asyncio.Future[dict[str, Any]],
        hard_timeout_grace: float,
    ) -> None:
        try:
            await self.notify("$/cancelRequest", {"id": request_id})
            await asyncio.wait_for(
                asyncio.shield(future), timeout=hard_timeout_grace
            )
        except TimeoutError:
            self.restart_required_after_external_cancellation = True
            future.cancel()
        except Exception:
            self.restart_required_after_external_cancellation = True
            future.cancel()
        finally:
            self._pending.pop(request_id, None)

    def _reserve_request(self) -> int:
        request_id = self._next_request_id
        self._next_request_id += 1
        self._pending[request_id] = asyncio.get_running_loop().create_future()
        return request_id

    async def _send(self, message: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None:
            raise RocqLspProcessExited("coq-lsp is not running")
        body = json.dumps(message, separators=(",", ":")).encode("utf-8")
        process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
        process.stdin.write(body)
        await process.stdin.drain()

    async def _reader_loop(self) -> None:
        try:
            while True:
                message = await self._read_message()
                if message is None:
                    raise RocqLspProcessExited("coq-lsp stdout closed")
                await self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(exc)

    async def _read_message(self) -> dict[str, Any] | None:
        process = self.process
        if process is None or process.stdout is None:
            raise RocqLspProcessExited("coq-lsp is not running")

        content_length: int | None = None
        while True:
            line = await process.stdout.readline()
            if line == b"":
                return None
            line = line.strip()
            if not line:
                continue
            name, _, value = line.decode("ascii", errors="replace").partition(":")
            if name.lower() == "content-length":
                content_length = int(value.strip())
            break

        while True:
            line = await process.stdout.readline()
            if line in (b"\r\n", b"\n", b""):
                break

        if content_length is None:
            raise ValueError("Missing Content-Length header from coq-lsp")

        body = await process.stdout.readexactly(content_length)
        return json.loads(body.decode("utf-8"))

    async def _dispatch(self, message: dict[str, Any]) -> None:
        if "id" in message and ("result" in message or "error" in message):
            request_id = message["id"]
            future = self._pending.get(request_id)
            if future is not None and not future.done():
                future.set_result(message)
            return

        method = message.get("method")
        params = message.get("params")
        if "id" in message and method:
            await self._respond_to_server_request(message["id"], method, params)
            return

        if isinstance(method, str):
            self.notifications.handle(method, params)

    async def _respond_to_server_request(
        self, request_id: int, method: str, params: Any | None
    ) -> None:
        if method == "workspace/workspaceFolders":
            result: Any = [{"uri": self.workspace.as_uri(), "name": self.workspace.name}]
        elif method == "workspace/configuration":
            items = (params or {}).get("items", []) if isinstance(params, dict) else []
            result = [rocq_lsp_settings(self.max_errors) for _ in items]
        else:
            result = None
        await self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    async def _drain_stderr(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        try:
            while line := await process.stderr.readline():
                self.stderr_lines.append(line.decode("utf-8", errors="replace").rstrip())
                if len(self.stderr_lines) > 200:
                    del self.stderr_lines[:100]
        except asyncio.CancelledError:
            raise

    @staticmethod
    def _result_or_raise(raw: dict[str, Any]) -> dict[str, Any]:
        if "error" in raw:
            raise RocqLspResponseError(raw["error"] or {})
        result = raw.get("result")
        if isinstance(result, dict):
            return result
        return {"result": result}

    def _ensure_launcher_available(self) -> None:
        launcher = self.command[0]
        if _looks_like_path(launcher):
            launcher_path = Path(launcher)
            if not launcher_path.is_absolute():
                launcher_path = self.workspace / launcher_path
            if launcher_path.exists():
                return
        elif shutil.which(launcher) is not None:
            return
        raise RocqLspUnavailableError(self._command_not_found_message(launcher))

    def _command_not_found_message(self, program: str) -> str:
        tried = shlex.join(self.command)
        if program == "opam":
            advice = (
                "Install opam, or set ROCQ_LSP_USE_OPAM=0 if ROCQ_LSP_BINARY "
                "points to a directly runnable coq-lsp binary."
            )
        else:
            advice = (
                "Install bens-rocq-lsp/coq-lsp, set ROCQ_LSP_BINARY to its full "
                "path, or set ROCQ_LSP_USE_OPAM=1 to run it through an opam switch."
            )
        if _looks_like_path(program):
            reason = f"executable path '{program}' was not found"
        else:
            reason = f"required executable '{program}' was not found on PATH"
        return f"Unable to start coq-lsp: {reason}. Tried command: {tried}. {advice}"

    async def _startup_failure_message(self, exc: Exception) -> str:
        await self._collect_startup_stderr()
        message = (
            "Unable to start coq-lsp: the server exited or failed during "
            f"initialization. Tried command: {shlex.join(self.command)}. "
            f"Workspace: {self.workspace}. Cause: {exc}."
        )
        stderr = "\n".join(self.stderr_lines[-20:]).strip()
        if stderr:
            message += f" stderr:\n{stderr}"
        message += (
            " Ensure bens-rocq-lsp/coq-lsp is installed in the selected opam "
            "switch or set ROCQ_LSP_BINARY/ROCQ_LSP_USE_OPAM to the correct "
            "launcher."
        )
        return message

    async def _collect_startup_stderr(self) -> None:
        process = self.process
        if process is not None and process.returncode is None:
            with suppress(Exception):
                await asyncio.wait_for(process.wait(), timeout=0.2)
        stderr_task = self._stderr_task
        if stderr_task is not None and not stderr_task.done():
            with suppress(Exception):
                await asyncio.wait_for(asyncio.shield(stderr_task), timeout=0.2)


def _looks_like_path(program: str) -> bool:
    return "/" in program
