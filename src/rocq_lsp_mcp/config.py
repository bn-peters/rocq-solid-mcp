from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_COMMAND_TIMEOUT_SECONDS = 5.0
DEFAULT_OVERALL_TIMEOUT_SECONDS = 55.0
MAX_OVERALL_TIMEOUT_SECONDS = 3600.0


@dataclass(frozen=True)
class TimeoutPolicy:
    request_seconds: float = DEFAULT_OVERALL_TIMEOUT_SECONDS
    cancel_grace_seconds: float = 5.0
    command_seconds: float | None = DEFAULT_COMMAND_TIMEOUT_SECONDS


@dataclass(frozen=True)
class RocqLspConfig:
    binary: str
    timeout_policy: TimeoutPolicy
    max_errors: int
    workspace: Path | None
    use_opam: bool = True
    idle_ttl_seconds: float = 1800.0


def _float_env(name: str, default: str) -> float:
    return float(os.environ.get(name, default))


def _optional_positive_float_env(name: str, default: str = "0") -> float | None:
    value = float(os.environ.get(name, default))
    if value <= 0:
        return None
    return value


def bounded_overall_timeout_seconds(value: float | None) -> float:
    if value is None:
        return DEFAULT_OVERALL_TIMEOUT_SECONDS
    return min(float(value), MAX_OVERALL_TIMEOUT_SECONDS)


def _int_env(name: str, default: str) -> int:
    return int(os.environ.get(name, default))


def _bool_env(name: str, default: str) -> bool:
    value = os.environ.get(name, default).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def load_config() -> RocqLspConfig:
    workspace = os.environ.get("ROCQ_LSP_WORKSPACE")
    return RocqLspConfig(
        binary=os.environ.get("ROCQ_LSP_BINARY", "coq-lsp"),
        timeout_policy=TimeoutPolicy(
            request_seconds=bounded_overall_timeout_seconds(
                _float_env("ROCQ_LSP_TIMEOUT", str(DEFAULT_OVERALL_TIMEOUT_SECONDS))
            ),
            cancel_grace_seconds=_float_env("ROCQ_LSP_HARD_TIMEOUT_GRACE", "5"),
            command_seconds=_optional_positive_float_env(
                "ROCQ_LSP_COMMAND_TIMEOUT", str(DEFAULT_COMMAND_TIMEOUT_SECONDS)
            ),
        ),
        max_errors=_int_env("ROCQ_LSP_MAX_ERRORS", "1000000"),
        workspace=Path(workspace).expanduser().resolve() if workspace else None,
        use_opam=_bool_env("ROCQ_LSP_USE_OPAM", "1"),
        idle_ttl_seconds=_float_env("ROCQ_LSP_IDLE_TTL_SECONDS", "1800"),
    )


def rocq_lsp_command(binary: str, *, use_opam: bool) -> tuple[str, ...]:
    if use_opam:
        return ("opam", "exec", "--", binary)
    return (binary,)


def rocq_lsp_settings(max_errors: int) -> dict[str, object]:
    return {
        "client_version": "any",
        "eager_diagnostics": True,
        "max_errors": max_errors,
        "check_only_on_request": False,
        "send_perf_data": True,
        "send_execinfo": True,
        "send_diags": True,
        "send_diags_extra_data": True,
    }
