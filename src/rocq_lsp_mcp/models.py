from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


def lsp_to_jsonable(value: Any) -> Any:
    """Convert common structured values into JSON-compatible data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): lsp_to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [lsp_to_jsonable(v) for v in value]
    if hasattr(value, "value"):
        return lsp_to_jsonable(value.value)
    if hasattr(value, "__dict__"):
        return {
            key: lsp_to_jsonable(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return str(value)


class Diagnostic(BaseModel):
    range: dict[str, Any]
    message: str
    severity: int | None = None
    code: str | int | None = None
    source: str | None = None
    data: Any | None = None
    proof_name: str | None = None
    proof_state: Any | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_lsp(cls, diagnostic: Any, proof_state: Any | None = None) -> "Diagnostic":
        raw = lsp_to_jsonable(diagnostic)
        data = raw.get("data")
        proof_name = data.get("proofName") if isinstance(data, dict) else None
        return cls(
            range=raw.get("range", {}),
            message=raw.get("message", ""),
            severity=raw.get("severity"),
            code=raw.get("code"),
            source=raw.get("source"),
            data=data,
            proof_name=proof_name if isinstance(proof_name, str) else None,
            proof_state=proof_state,
            raw=raw,
        )


class CompileResult(BaseModel):
    status: Literal["complete", "timeout", "failed"]
    file: str
    uri: str | None = None
    workspace: str | None = None
    version: int | None = None
    completed: dict[str, Any] | None = None
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    errors: list[Diagnostic] = Field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    timed_out: bool = False
    restarted: bool = False
    message: str | None = None

    @classmethod
    def from_diagnostics(
        cls,
        *,
        status: Literal["complete", "timeout", "failed"],
        file: str,
        uri: str | None,
        workspace: str | None,
        version: int | None,
        completed: dict[str, Any] | None,
        diagnostics: list[Diagnostic],
        timed_out: bool = False,
        restarted: bool = False,
        message: str | None = None,
    ) -> "CompileResult":
        diagnostics = _agent_facing_diagnostics(diagnostics)
        errors = [diag for diag in diagnostics if diag.severity == 1]
        warnings = [diag for diag in diagnostics if diag.severity == 2]
        infos = [diag for diag in diagnostics if diag.severity in (3, 4)]
        return cls(
            status=status,
            file=file,
            uri=uri,
            workspace=workspace,
            version=version,
            completed=completed,
            diagnostics=diagnostics,
            errors=errors,
            error_count=len(errors),
            warning_count=len(warnings),
            info_count=len(infos),
            timed_out=timed_out,
            restarted=restarted,
            message=message,
        )


def _agent_facing_diagnostics(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    seen_error_proofs: set[str] = set()
    filtered: list[Diagnostic] = []
    for diagnostic in diagnostics:
        if diagnostic.severity == 1 and diagnostic.proof_name is not None:
            if diagnostic.proof_name in seen_error_proofs:
                continue
            seen_error_proofs.add(diagnostic.proof_name)
        filtered.append(diagnostic)
    return filtered
