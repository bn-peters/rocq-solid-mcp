from __future__ import annotations

import asyncio
import codecs
import json
import os
import signal
import shlex
import shutil
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .config import rocq_lsp_command, rocq_lsp_settings
from .notifications import NotificationStore


TimeoutKind = Literal["request", "command", "after_error"]
STDERR_CHUNK_SIZE = 64 * 1024


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


@dataclass(frozen=True)
class WatchdogTimeout:
    """The single, immutable outcome a watchdog publishes when it cancels."""

    kind: TimeoutKind
    seconds: float
    timed_out_range: dict[str, Any] | None


def language_id_for_path(path: Path) -> str:
    name = path.name
    if name.endswith(".mv"):
        return "markdown"
    if name.endswith(".lv") or name.endswith(".v.tex"):
        return "latex"
    return "rocq"


def _end_of_content_position(content: str | None) -> tuple[int, int] | None:
    """0-based (line, character) just past the last non-whitespace character.

    A sentence whose range ends at or beyond this position is the document's
    final sentence: only whitespace follows it. Precomputing it keeps the
    watchdog's final-sentence check O(1) per sentence instead of re-splitting
    the document content on every wake-up.
    """
    if content is None:
        return None
    stripped = content.rstrip()
    if not stripped:
        return (0, 0)
    lines = stripped.splitlines()
    return (len(lines) - 1, len(lines[-1]))


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
        self._reader_failure: RocqLspProcessExited | None = None
        self.stderr_lines: list[str] = []
        self.restart_required_after_external_cancellation = False

    async def start(self) -> None:
        self._ensure_launcher_available()
        self._reader_failure = None
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
                    "clientInfo": {"name": "rocq-solid-mcp"},
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
        watchdog: _CommandTimeoutWatchdog | None = None
        watchdog_task: asyncio.Task[None] | None = None
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
            watchdog = _CommandTimeoutWatchdog(
                client=self,
                uri=uri,
                version=version,
                request_id=request_id,
                command_timeout=command_timeout,
                timeout_after_error=timeout_after_error,
                content=content,
            )
            watchdog_task = asyncio.create_task(watchdog.run())

        try:
            if watchdog is None or watchdog_task is None:
                return await self._await_get_document(
                    request_id=request_id,
                    future=future,
                    timeout=timeout,
                    hard_timeout_grace=hard_timeout_grace,
                )

            done, _ = await asyncio.wait(
                {future, watchdog_task},
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
                if watchdog.timeout is not None and "error" in raw:
                    return self._get_document_timeout_result(raw, watchdog.timeout)
                return GetDocumentResult(document=self._result_or_raise(raw))

            await watchdog_task
            if watchdog.timeout is None:
                # The watchdog stopped without cancelling anything (it is
                # advisory and contains its own failures), so keep waiting
                # for the document under the plain request deadline.
                return await self._await_get_document(
                    request_id=request_id,
                    future=future,
                    timeout=timeout,
                    hard_timeout_grace=hard_timeout_grace,
                )
            if future.done():
                raw = future.result()
                self._pending.pop(request_id, None)
                if "error" in raw:
                    return self._get_document_timeout_result(raw, watchdog.timeout)
                return GetDocumentResult(document=self._result_or_raise(raw))
            return await self._wait_for_cancelled_get_document(
                request_id=request_id,
                future=future,
                hard_timeout_grace=hard_timeout_grace,
                timeout_kind=watchdog.timeout.kind,
                timeout_seconds=watchdog.timeout.seconds,
                timed_out_range=watchdog.timeout.timed_out_range,
            )

        except asyncio.CancelledError:
            await self._cancel_lsp_request_after_external_cancellation(
                request_id=request_id,
                future=future,
                hard_timeout_grace=hard_timeout_grace,
            )
            raise
        finally:
            if watchdog_task is not None:
                watchdog_task.cancel()
                with suppress(asyncio.CancelledError):
                    await watchdog_task

    async def _await_get_document(
        self,
        *,
        request_id: int,
        future: asyncio.Future[dict[str, Any]],
        timeout: float | None,
        hard_timeout_grace: float,
    ) -> GetDocumentResult:
        try:
            raw = await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
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

    def _get_document_timeout_result(
        self, raw: dict[str, Any], watchdog_timeout: WatchdogTimeout
    ) -> GetDocumentResult:
        document = {} if "error" in raw else self._result_or_raise(raw)
        return GetDocumentResult(
            document=document,
            timed_out=True,
            timeout_kind=watchdog_timeout.kind,
            timed_out_range=watchdog_timeout.timed_out_range,
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
        if self._reader_failure is not None:
            raise self._reader_failure
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
            failure = (
                exc
                if isinstance(exc, RocqLspProcessExited)
                else RocqLspProcessExited(f"coq-lsp JSON-RPC reader failed: {exc}")
            )
            self._reader_failure = failure
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(failure)

    async def _read_message(self) -> dict[str, Any] | None:
        process = self.process
        if process is None or process.stdout is None:
            raise RocqLspProcessExited("coq-lsp is not running")

        content_length: int | None = None
        saw_header = False
        while True:
            line = await process.stdout.readline()
            if line == b"":
                if not saw_header:
                    return None
                raise RocqLspProcessExited("coq-lsp stdout closed during headers")
            if line in (b"\r\n", b"\n"):
                if saw_header:
                    break
                continue
            saw_header = True
            name, separator, value = line.decode(
                "ascii", errors="replace"
            ).rstrip("\r\n").partition(":")
            if not separator:
                raise ValueError("Malformed JSON-RPC header from coq-lsp")
            if name.lower() == "content-length":
                if content_length is not None:
                    raise ValueError("Duplicate Content-Length header from coq-lsp")
                content_length = int(value.strip())

        if content_length is None:
            raise ValueError("Missing Content-Length header from coq-lsp")
        if content_length < 0:
            raise ValueError("Negative Content-Length header from coq-lsp")

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
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        try:
            while chunk := await process.stderr.read(STDERR_CHUNK_SIZE):
                pending += decoder.decode(chunk)
                lines = pending.split("\n")
                pending = lines.pop()
                for line in lines:
                    self._append_stderr_line(line.rstrip("\r"))
                # A diagnostic need not contain a newline. Store fragments so
                # draining never depends on asyncio's line-length limit.
                if len(pending) >= STDERR_CHUNK_SIZE:
                    self._append_stderr_line(pending)
                    pending = ""
            pending += decoder.decode(b"", final=True)
            if pending:
                self._append_stderr_line(pending.rstrip("\r"))
        except asyncio.CancelledError:
            raise

    def _append_stderr_line(self, line: str) -> None:
        self.stderr_lines.append(line)
        if len(self.stderr_lines) > 200:
            del self.stderr_lines[:100]

    async def process_failure_message(self, exc: BaseException) -> str:
        """Add child exit status and recent stderr to a transport failure."""
        await self._collect_process_stderr()
        message = str(exc)
        process = self.process
        returncode = process.returncode if process is not None else None
        if returncode is not None:
            if returncode < 0:
                signal_number = -returncode
                try:
                    signal_name = signal.Signals(signal_number).name
                except ValueError:
                    signal_name = "unknown signal"
                message += (
                    f". coq-lsp exited with signal {signal_name} "
                    f"({signal_number})"
                )
            else:
                message += f". coq-lsp exited with code {returncode}"
        stderr = "\n".join(self.stderr_lines[-20:]).strip()
        if stderr:
            message += f". stderr:\n{stderr}"
        return message

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
        await self._collect_process_stderr()
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

    async def _collect_process_stderr(self) -> None:
        process = self.process
        if process is not None and process.returncode is None:
            with suppress(Exception):
                await asyncio.wait_for(process.wait(), timeout=0.2)
        stderr_task = self._stderr_task
        if stderr_task is not None and not stderr_task.done():
            with suppress(Exception):
                await asyncio.wait_for(asyncio.shield(stderr_task), timeout=0.2)


class _CommandTimeoutWatchdog:
    """Cancels one ``coq/getDocument`` request when a per-sentence or
    after-error deadline expires.

    The interface rules that keep the timing behavior easy to reason about:

    - Every timer is an absolute event-loop instant. The per-sentence
      deadline is anchored to the sentence's ``$/coq/executionInformation``
      notification and the after-error deadline to the first observed error
      diagnostic, so unrelated wake-ups can never restart a timer.
    - The after-error deadline is armed in exactly one place, from the state
      poll at the top of the loop; the error waiter task only wakes the loop.
    - Which timeout fired is decided when the wait is armed — the earliest
      deadline wins, and the after-error deadline wins ties — never
      re-derived from the clock afterwards.
    - The outcome is published exactly once, as the immutable
      ``self.timeout``, before the cancel notification is sent.
    - The watchdog is advisory and contains its own failures: if sending the
      cancel fails (for example because coq-lsp died), the request future is
      the authoritative error channel and surfaces the real failure itself.
    """

    def __init__(
        self,
        *,
        client: RocqLspClient,
        uri: str,
        version: int,
        request_id: int,
        command_timeout: float | None,
        timeout_after_error: float | None,
        content: str | None,
    ) -> None:
        self._client = client
        self._uri = uri
        self._version = version
        self._request_id = request_id
        self._command_timeout = command_timeout
        self._timeout_after_error = timeout_after_error
        self._final_boundary = _end_of_content_position(content)
        self.timeout: WatchdogTimeout | None = None

    async def run(self) -> None:
        try:
            await self._watch()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Advisory task: any I/O failure it can hit (for example a dead
            # coq-lsp process while sending the cancel) also fails the
            # pending request future, which is the authoritative channel.
            return

    async def _watch(self) -> None:
        loop = asyncio.get_running_loop()
        notifications = self._client.notifications
        uri = self._uri
        version = self._version
        after_sequence = notifications.latest_execution_sequence(uri, version)
        active_range: dict[str, Any] | None = None
        sentence_deadline: float | None = None
        after_error_deadline: float | None = None
        watch_errors = self._timeout_after_error is not None
        exec_task: asyncio.Task[tuple[int, dict[str, Any]] | None] | None = None
        error_task: asyncio.Task[None] | None = None

        try:
            while True:
                if (
                    watch_errors
                    and after_error_deadline is None
                    and notifications.has_error_diagnostic(uri, version)
                ):
                    # The only site that arms the after-error deadline. It
                    # never resets, and it also covers the final sentence and
                    # end-of-file finalization, which the per-sentence
                    # deadline exempts: after an error a bounded exit matters
                    # more than attributing that time precisely.
                    after_error_deadline = loop.time() + self._timeout_after_error

                expiring = self._earliest_deadline(
                    sentence_deadline, after_error_deadline
                )
                if expiring is not None and expiring[0] <= loop.time():
                    await self._cancel(expiring, active_range)
                    return

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
                if watch_errors and after_error_deadline is None:
                    if error_task is None or error_task.done():
                        error_task = asyncio.create_task(
                            notifications.wait_for_error_diagnostic(uri, version)
                        )
                    waiters.add(error_task)

                done, _ = await asyncio.wait(
                    waiters,
                    timeout=(
                        None if expiring is None else expiring[0] - loop.time()
                    ),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done and expiring is not None:
                    await self._cancel(expiring, active_range)
                    return
                if exec_task in done:
                    update = exec_task.result()
                    if update is not None:
                        after_sequence, params = update
                        range_ = (
                            params.get("range") if isinstance(params, dict) else None
                        )
                        active_range = range_ if isinstance(range_, dict) else None
                        sentence_deadline = self._sentence_deadline_for(
                            active_range, loop.time()
                        )
                # A completed error waiter only wakes the loop; the poll at
                # the top of the next iteration arms the deadline.
        finally:
            for task in (exec_task, error_task):
                if task is not None and not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

    def _earliest_deadline(
        self,
        sentence_deadline: float | None,
        after_error_deadline: float | None,
    ) -> tuple[float, TimeoutKind, float] | None:
        """The next (deadline, kind, configured seconds) to enforce.

        The after-error deadline wins ties so a bounded post-error exit is
        reported as such.
        """
        expiring: tuple[float, TimeoutKind, float] | None = None
        for deadline, kind, seconds in (
            (after_error_deadline, "after_error", self._timeout_after_error),
            (sentence_deadline, "command", self._command_timeout),
        ):
            if deadline is None:
                continue
            if expiring is None or deadline < expiring[0]:
                expiring = (deadline, kind, seconds)
        return expiring

    def _sentence_deadline_for(
        self, active_range: dict[str, Any] | None, now: float
    ) -> float | None:
        """Absolute deadline for the sentence that just started, if any.

        The final sentence is exempt because execution notifications mark
        sentence starts, not completion, so end-of-file finalization would
        otherwise be attributed to it.
        """
        if (
            self._command_timeout is None
            or active_range is None
            or self._is_final_sentence(active_range)
        ):
            return None
        return now + self._command_timeout

    def _is_final_sentence(self, range_: dict[str, Any]) -> bool:
        if self._final_boundary is None:
            return False
        end = range_.get("end")
        if not isinstance(end, dict):
            return False
        line = end.get("line")
        character = end.get("character")
        if not isinstance(line, int) or not isinstance(character, int):
            return False
        return (line, character) >= self._final_boundary

    async def _cancel(
        self,
        expiring: tuple[float, TimeoutKind, float],
        timed_out_range: dict[str, Any] | None,
    ) -> None:
        _, kind, seconds = expiring
        self.timeout = WatchdogTimeout(
            kind=kind, seconds=seconds, timed_out_range=timed_out_range
        )
        await self._client.notify("$/cancelRequest", {"id": self._request_id})


def _looks_like_path(program: str) -> bool:
    return "/" in program
