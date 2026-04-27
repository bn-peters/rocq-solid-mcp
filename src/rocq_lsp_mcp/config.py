from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RocqLspConfig:
    binary: str
    timeout: float
    hard_timeout_grace: float
    max_errors: int
    workspace: Path | None


def _float_env(name: str, default: str) -> float:
    return float(os.environ.get(name, default))


def _int_env(name: str, default: str) -> int:
    return int(os.environ.get(name, default))


def load_config() -> RocqLspConfig:
    workspace = os.environ.get("ROCQ_LSP_WORKSPACE")
    return RocqLspConfig(
        binary=os.environ.get("ROCQ_LSP_BINARY", "coq-lsp"),
        timeout=_float_env("ROCQ_LSP_TIMEOUT", "60"),
        hard_timeout_grace=_float_env("ROCQ_LSP_HARD_TIMEOUT_GRACE", "5"),
        max_errors=_int_env("ROCQ_LSP_MAX_ERRORS", "1000000"),
        workspace=Path(workspace).expanduser().resolve() if workspace else None,
    )


def rocq_lsp_settings(max_errors: int) -> dict[str, object]:
    return {
        "client_version": "any",
        "eager_diagnostics": True,
        "max_errors": max_errors,
        "check_only_on_request": False,
        "send_perf_data": False,
        "send_diags": True,
        "send_diags_extra_data": True,
    }
