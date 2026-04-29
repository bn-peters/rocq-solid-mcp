from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress
from pathlib import Path
from typing import Any

from .config import rocq_lsp_settings
from .notifications import NotificationStore


class RocqLspResponseError(RuntimeError):
    def __init__(self, error: dict[str, Any]) -> None:
        self.error = error
        super().__init__(str(error))


class RocqLspHardTimeout(TimeoutError):
    pass


class RocqLspProcessExited(RuntimeError):
    pass


def language_id_for_path(path: Path) -> str:
    name = path.name
    if name.endswith(".mv"):
        return "markdown"
    if name.endswith(".lv") or name.endswith(".v.tex"):
        return "latex"
    return "rocq"


class RocqLspClient:
    def __init__(
        self,
        *,
        binary: str,
        workspace: Path,
        max_errors: int,
        request_timeout: float,
        notifications: NotificationStore,
    ) -> None:
        self.binary = binary
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

    async def start(self) -> None:
        self.process = await asyncio.create_subprocess_exec(
            self.binary,
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
                "clientInfo": {"name": "rocq-lsp-mcp-prototype"},
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
        timeout: float,
        hard_timeout_grace: float,
        goals: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        request_id = self._reserve_request()
        future = self._pending[request_id]
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
            except TimeoutError:
                self._pending.pop(request_id, None)
                future.cancel()
                raise RocqLspHardTimeout(
                    f"coq/getDocument timed out after {timeout}s and did not "
                    f"settle within {hard_timeout_grace}s"
                ) from exc
            finally:
                self._pending.pop(request_id, None)

            if "error" in raw:
                return {}, True
            return self._result_or_raise(raw), True

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
