from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_serializer

DEFAULT_MESSAGE_LIMIT = 10
MAX_MESSAGE_LIMIT = 50


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
    range: dict[str, Any] = Field(default_factory=dict, exclude=True)
    location: str | None = None
    message: str
    severity: int | None = None
    code: str | int | None = None
    source: str | None = None
    data: Any | None = Field(default=None, exclude=True)
    proof_name: str | None = None
    proof_state: Any | None = None
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @classmethod
    def from_lsp(cls, diagnostic: Any, proof_state: Any | None = None) -> "Diagnostic":
        raw = lsp_to_jsonable(diagnostic)
        data = raw.get("data")
        proof_name = data.get("proofName") if isinstance(data, dict) else None
        raw_range = raw.get("range", {})
        range_ = raw_range if isinstance(raw_range, dict) else {}
        return cls(
            range=range_,
            location=location_string(range_),
            message=raw.get("message", ""),
            severity=raw.get("severity"),
            code=raw.get("code"),
            source=raw.get("source"),
            data=data,
            proof_name=proof_name if isinstance(proof_name, str) else None,
            proof_state=proof_state if not is_empty_value(proof_state) else None,
            raw=raw,
        )

    @model_serializer
    def serialize(self) -> dict[str, Any]:
        return pruned_dict(
            {
                "level": diagnostic_level(self.severity),
                "message": self.message,
                "location": self.location,
                "code": self.code,
                "source": self.source,
                "proof_name": self.proof_name,
                "proof_state": self.proof_state,
            }
        )


class CommandOutput(BaseModel):
    severity: int | None = None
    text: str

    @classmethod
    def from_feedback(cls, feedback: Any) -> "CommandOutput | None":
        raw = lsp_to_jsonable(feedback)
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            severity = raw[0] if isinstance(raw[0], int) else None
            text = raw[1]
            return cls(severity=severity, text=str(text))
        if isinstance(raw, dict):
            severity = raw.get("level")
            text = raw.get("text")
            if text is None:
                text = raw.get("message")
            if text is None:
                return None
            return cls(
                severity=severity if isinstance(severity, int) else None,
                text=str(text),
            )
        if raw is None:
            return None
        return cls(text=str(raw))

    @model_serializer
    def serialize(self) -> dict[str, Any]:
        return pruned_dict(
            {
                "level": diagnostic_level(self.severity),
                "text": self.text,
            }
        )


class CommandResult(BaseModel):
    status: Literal["complete", "timeout", "failed"]
    file: str
    command: str
    location: str | None = None
    output: list[CommandOutput] = Field(default_factory=list)
    uri: str | None = None
    workspace: str | None = None
    version: int | None = None
    timed_out: bool = False
    restarted: bool = False
    message: str | None = None

    @classmethod
    def from_feedback(
        cls,
        *,
        status: Literal["complete", "timeout", "failed"],
        file: str,
        command: str,
        location: str | None,
        uri: str | None,
        workspace: str | None,
        version: int | None,
        feedback: Any,
        timed_out: bool = False,
        restarted: bool = False,
        message: str | None = None,
    ) -> "CommandResult":
        output = [
            item
            for entry in _feedback_entries(feedback)
            if (item := CommandOutput.from_feedback(entry)) is not None
        ]
        return cls(
            status=status,
            file=file,
            command=command,
            location=location,
            output=output,
            uri=uri,
            workspace=workspace,
            version=version,
            timed_out=timed_out,
            restarted=restarted,
            message=message,
        )

    @model_serializer
    def serialize(self) -> dict[str, Any]:
        return pruned_dict(
            {
                "status": self.status,
                "file": display_file(self.file, self.workspace),
                "location": self.location,
                "command": self.command,
                "output": [item.model_dump() for item in self.output],
                "timed_out": True if self.timed_out else None,
                "restarted": True if self.restarted else None,
                "message": self.message,
            }
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
    omitted_error_count: int = 0
    omitted_warning_count: int = 0
    omitted_info_count: int = 0
    message_limit: int = DEFAULT_MESSAGE_LIMIT
    timed_out: bool = False
    restarted: bool = False
    timeout_context: dict[str, Any] | None = None
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
        message_limit: int | None = DEFAULT_MESSAGE_LIMIT,
        timed_out: bool = False,
        restarted: bool = False,
        timeout_context: dict[str, Any] | None = None,
        message: str | None = None,
    ) -> "CompileResult":
        message_limit = bounded_message_limit(message_limit)
        buckets = diagnostic_buckets(diagnostics)
        limited_diagnostics = limit_diagnostics(diagnostics, message_limit)
        limited_buckets = diagnostic_buckets(limited_diagnostics)
        omitted_errors = len(buckets["errors"]) - len(limited_buckets["errors"])
        omitted_warnings = len(buckets["warnings"]) - len(limited_buckets["warnings"])
        omitted_infos = len(buckets["infos"]) - len(limited_buckets["infos"])
        return cls(
            status=status,
            file=file,
            uri=uri,
            workspace=workspace,
            version=version,
            completed=completed,
            diagnostics=limited_diagnostics,
            errors=limited_buckets["errors"],
            error_count=len(buckets["errors"]),
            warning_count=len(buckets["warnings"]),
            info_count=len(buckets["infos"]),
            omitted_error_count=omitted_errors,
            omitted_warning_count=omitted_warnings,
            omitted_info_count=omitted_infos,
            message_limit=message_limit,
            timed_out=timed_out,
            restarted=restarted,
            timeout_context=timeout_context,
            message=message,
        )

    @model_serializer
    def serialize(self) -> dict[str, Any]:
        omitted_counts = diagnostic_counts(
            errors=self.omitted_error_count,
            warnings=self.omitted_warning_count,
            infos=self.omitted_info_count,
            force=True,
        )
        has_omissions = bool(omitted_counts)
        return pruned_dict(
            {
                "status": self.status,
                "file": display_file(self.file, self.workspace),
                "counts": diagnostic_counts(
                    errors=self.error_count,
                    warnings=self.warning_count,
                    infos=self.info_count,
                    force=has_omissions,
                ),
                "omitted_counts": omitted_counts,
                "message_limit": self.message_limit if has_omissions else None,
                "diagnostics": [
                    diagnostic.model_dump() for diagnostic in self.diagnostics
                ],
                "timed_out": True if self.timed_out else None,
                "restarted": True if self.restarted else None,
                "timeout_context": (
                    self.timeout_context if self.status == "timeout" else None
                ),
                "message": self.message,
            }
        )


def bounded_message_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_MESSAGE_LIMIT
    return min(max(limit, 0), MAX_MESSAGE_LIMIT)


def diagnostic_buckets(diagnostics: list[Diagnostic]) -> dict[str, list[Diagnostic]]:
    buckets: dict[str, list[Diagnostic]] = {
        "errors": [],
        "warnings": [],
        "infos": [],
    }
    for diagnostic in diagnostics:
        buckets[diagnostic_bucket(diagnostic)].append(diagnostic)
    return buckets


def limit_diagnostics(
    diagnostics: list[Diagnostic], message_limit: int
) -> list[Diagnostic]:
    counts = {"errors": 0, "warnings": 0, "infos": 0}
    result: list[Diagnostic] = []
    for diagnostic in diagnostics:
        bucket = diagnostic_bucket(diagnostic)
        if counts[bucket] >= message_limit:
            continue
        counts[bucket] += 1
        result.append(diagnostic)
    return result


def diagnostic_bucket(diagnostic: Diagnostic) -> str:
    if diagnostic.severity == 1:
        return "errors"
    if diagnostic.severity == 2:
        return "warnings"
    return "infos"


def location_string(range_: Any) -> str | None:
    """Render an LSP range as a compact human-facing 1-based location."""
    if not isinstance(range_, dict):
        return None
    start = _position(range_.get("start"))
    end = _position(range_.get("end"))
    if start is None or end is None:
        return None
    return f"{start[0] + 1}:{start[1] + 1} to {end[0] + 1}:{end[1] + 1}"


def diagnostic_level(severity: int | None) -> str | None:
    if severity == 1:
        return "error"
    if severity == 2:
        return "warning"
    if severity in (3, 4):
        return "info"
    return None


def command_location(line: int, character: int) -> str:
    return f"{line}:{character}"


def diagnostic_counts(
    *, errors: int, warnings: int, infos: int, force: bool = False
) -> dict[str, int]:
    if warnings == 0 and not force:
        return {}
    return {
        key: count
        for key, count in {
            "errors": errors,
            "warnings": warnings,
            "infos": infos,
        }.items()
        if count > 0
    }


def display_file(file: str, workspace: str | None) -> str:
    path = Path(file)
    if workspace is not None:
        try:
            return path.resolve().relative_to(Path(workspace).resolve()).as_posix()
        except ValueError:
            pass
    return file


def pruned_dict(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        pruned = prune_empty(item)
        if not is_empty_value(pruned):
            result[key] = pruned
    return result


def prune_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return pruned_dict(value)
    if isinstance(value, list):
        return [
            pruned
            for item in value
            if not is_empty_value(pruned := prune_empty(item))
        ]
    return value


def is_empty_value(value: Any) -> bool:
    return value is None or value == [] or value == {}


def _position(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, dict):
        return None
    line = value.get("line")
    character = value.get("character")
    if isinstance(line, int) and isinstance(character, int):
        return line, character
    return None


def _feedback_entries(feedback: Any) -> list[Any]:
    raw = lsp_to_jsonable(feedback)
    if isinstance(raw, dict):
        entries = raw.get("feedback")
        return entries if isinstance(entries, list) else []
    if isinstance(raw, list):
        return raw
    return []
