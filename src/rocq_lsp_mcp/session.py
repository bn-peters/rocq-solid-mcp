from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from pathlib import Path

from .config import RocqLspConfig
from .notifications import NotificationStore
from .rocq_client import RocqLspClient


class RocqLspSession:
    def __init__(
        self,
        *,
        workspace: Path,
        config: RocqLspConfig,
    ) -> None:
        self.workspace = workspace
        self.config = config
        self.lock = asyncio.Lock()
        self.active_requests = 0
        self.last_used_at = time.monotonic()
        self.versions: dict[str, int] = {}
        self._client: RocqLspClient | None = None
        self._notifications = NotificationStore()

    @property
    def client(self) -> RocqLspClient:
        if self._client is None:
            raise RuntimeError("rocq-lsp session has not been started")
        return self._client

    @property
    def notifications(self) -> NotificationStore:
        return self._notifications

    async def start(self) -> None:
        if self._client is not None:
            raise RuntimeError("rocq-lsp session has already been started")
        self.versions.clear()
        self._notifications = NotificationStore()
        request_timeout = max(
            10.0,
            self.config.timeout_policy.request_seconds
            + self.config.timeout_policy.cancel_grace_seconds
            + 5,
        )
        client = RocqLspClient(
            binary=self.config.binary,
            use_opam=self.config.use_opam,
            workspace=self.workspace,
            max_errors=self.config.max_errors,
            request_timeout=request_timeout,
            notifications=self._notifications,
        )
        self._client = client
        try:
            await client.start()
            await client.notify_change_configuration()
        except Exception:
            await self.stop(hard=True)
            raise

    async def restart(self) -> None:
        await self.stop(hard=True)
        await self.start()

    async def stop(self, *, hard: bool = False) -> None:
        client = self._client
        self._client = None
        if client is None:
            return

        if hard:
            with suppress(Exception):
                client.clear_pending_requests()
            with suppress(Exception):
                await client.kill_server()
            return

        with suppress(Exception):
            await client.close()
