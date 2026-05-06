from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .config import DEFAULT_MESSAGE_LIMIT, bounded_message_limit

DEFAULT_LONG_RUNNING_SECONDS = 3.0
MAX_LONG_RUNNING_COMMANDS = 10
LocationFormat = Literal["range", "point"]
DiagnosticSeverity = Literal["info", "warning", "error"]
DEFAULT_DIAGNOSTIC_SEVERITY: DiagnosticSeverity = "error"
DIAGNOSTIC_LEVEL_PRIORITY = {
    "info": 1,
    "warning": 2,
    "error": 3,
}


@dataclass(frozen=True)
class RenderOptions:
    include_proof_state: bool = True
    include_timeout_context: bool = True
    message_limit: int | None = DEFAULT_MESSAGE_LIMIT
    severity: DiagnosticSeverity = DEFAULT_DIAGNOSTIC_SEVERITY
    location_format: LocationFormat = "range"
    deduplicate_proof_errors: bool = True


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
    range: dict[str, Any] = Field(default_factory=dict)
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
        raw_range = raw.get("range", {})
        range_ = raw_range if isinstance(raw_range, dict) else {}
        return cls(
            range=range_,
            message=raw.get("message", ""),
            severity=raw.get("severity"),
            code=raw.get("code"),
            source=raw.get("source"),
            data=data,
            proof_name=proof_name if isinstance(proof_name, str) else None,
            proof_state=proof_state if not is_empty_value(proof_state) else None,
            raw=raw,
        )

    def render(self, options: RenderOptions | None = None) -> dict[str, Any]:
        options = options or RenderOptions()
        return pruned_dict(
            {
                "level": diagnostic_level(self.severity),
                "message": self.message,
                "location": location_string(self.range, options.location_format),
                "code": self.code,
                "source": self.source,
                "proof_name": self.proof_name,
                "proof_state": (
                    self.proof_state if options.include_proof_state else None
                ),
            }
        )


class RunAtOutput(BaseModel):
    severity: int | None = None
    text: str

    @classmethod
    def from_feedback(cls, feedback: Any) -> "RunAtOutput | None":
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

    def render(self, options: RenderOptions | None = None) -> dict[str, Any]:
        return pruned_dict(
            {
                "level": diagnostic_level(self.severity),
                "text": self.text,
            }
        )


class LongRunningCommand(BaseModel):
    range: dict[str, Any] = Field(default_factory=dict)
    text: str
    time_seconds: float

    def render(self, options: RenderOptions | None = None) -> dict[str, Any]:
        options = options or RenderOptions()
        return pruned_dict(
            {
                "location": location_string(self.range, options.location_format),
                "text": self.text,
            }
        )


class RunAtResult(BaseModel):
    status: Literal["complete", "timeout", "failed"]
    file: str
    commands: str
    location: str | None = None
    output: list[RunAtOutput] = Field(default_factory=list)
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
        commands: str,
        location: str | None,
        uri: str | None,
        workspace: str | None,
        version: int | None,
        feedback: Any,
        timed_out: bool = False,
        restarted: bool = False,
        message: str | None = None,
    ) -> "RunAtResult":
        output = [
            item
            for entry in _feedback_entries(feedback)
            if (item := RunAtOutput.from_feedback(entry)) is not None
        ]
        return cls(
            status=status,
            file=file,
            commands=commands,
            location=location,
            output=output,
            uri=uri,
            workspace=workspace,
            version=version,
            timed_out=timed_out,
            restarted=restarted,
            message=message,
        )

    def render(self, options: RenderOptions | None = None) -> dict[str, Any]:
        options = options or RenderOptions()
        return pruned_dict(
            {
                "status": self.status,
                "file": display_file(self.file, self.workspace),
                "location": self.location,
                "commands": self.commands,
                "output": [item.render(options) for item in self.output],
                "timed_out": True if self.timed_out else None,
                "restarted": True if self.restarted else None,
                "message": self.message,
            }
        )


class TimeoutContext(BaseModel):
    operation: str = "coq/getDocument"
    timeout_kind: str = "request"
    request_timeout_seconds: int | float | None = None
    cancel_grace_seconds: int | float
    checked_until: dict[str, int] | None = None
    latest_processing_range: dict[str, Any] | None = None
    next_unchecked: dict[str, Any] | None = None
    command_timeout_seconds: int | float | None = None
    timed_out_range: dict[str, Any] | None = None
    timed_out_command: dict[str, Any] | None = None

    @classmethod
    def from_manager_state(
        cls,
        *,
        content: str,
        document: dict[str, Any] | None,
        progress: dict[str, Any] | None,
        request_timeout_seconds: int | float,
        cancel_grace_seconds: int | float,
        timeout_kind: str = "request",
        command_timeout_seconds: int | float | None = None,
        timed_out_range: dict[str, Any] | None = None,
    ) -> "TimeoutContext":
        completed = _completed_only(document) if document else None
        completed_range = completed.get("range") if completed else None
        completed_end = (
            range_end(completed_range) if isinstance(completed_range, dict) else None
        )
        processing_range = _latest_processing_range(progress)
        progress_start = (
            range_start(processing_range)
            if isinstance(processing_range, dict)
            else None
        )
        checked_until = completed_end or progress_start

        return cls(
            timeout_kind=timeout_kind,
            request_timeout_seconds=request_timeout_seconds,
            cancel_grace_seconds=cancel_grace_seconds,
            checked_until=checked_until,
            latest_processing_range=processing_range,
            next_unchecked=_next_unchecked_preview(content, checked_until),
            command_timeout_seconds=command_timeout_seconds,
            timed_out_range=timed_out_range,
            timed_out_command=(
                _source_preview_for_range(content, timed_out_range)
                if timed_out_range is not None
                else None
            ),
        )

    def render(self, options: RenderOptions | None = None) -> dict[str, Any]:
        options = options or RenderOptions()
        return pruned_dict(
            {
                "operation": self.operation,
                "timeout_kind": self.timeout_kind,
                "request_timeout_seconds": self.request_timeout_seconds,
                "cancel_grace_seconds": self.cancel_grace_seconds,
                "checked_until": position_string(self.checked_until),
                "latest_processing_range": location_string(
                    self.latest_processing_range,
                    options.location_format,
                ),
                "next_unchecked": self.next_unchecked,
                "command_timeout_seconds": self.command_timeout_seconds,
                "timed_out_range": location_string(
                    self.timed_out_range,
                    options.location_format,
                ),
                "timed_out_command": self.timed_out_command,
            }
        )


class CheckResult(BaseModel):
    status: Literal["complete", "timeout", "failed"]
    file: str
    uri: str | None = None
    workspace: str | None = None
    version: int | None = None
    completed: dict[str, Any] | None = None
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    timed_out: bool = False
    restarted: bool = False
    timeout_context: TimeoutContext | None = None
    long_running_commands: list[LongRunningCommand] = Field(default_factory=list)
    message: str | None = None

    @property
    def errors(self) -> list[Diagnostic]:
        return diagnostic_buckets(self.diagnostics)["errors"]

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(diagnostic_buckets(self.diagnostics)["warnings"])

    @property
    def info_count(self) -> int:
        return len(diagnostic_buckets(self.diagnostics)["infos"])

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
        timeout_context: TimeoutContext | None = None,
        long_running_commands: list[LongRunningCommand] | None = None,
        message: str | None = None,
    ) -> "CheckResult":
        return cls(
            status=status,
            file=file,
            uri=uri,
            workspace=workspace,
            version=version,
            completed=completed,
            diagnostics=diagnostics,
            timed_out=timed_out,
            restarted=restarted,
            timeout_context=timeout_context,
            long_running_commands=long_running_commands or [],
            message=message,
        )

    def render(self, options: RenderOptions | None = None) -> dict[str, Any]:
        options = options or RenderOptions()
        message_limit = (
            None
            if options.message_limit is None
            else bounded_message_limit(options.message_limit)
        )
        diagnostics = _render_diagnostics(self.diagnostics, options)
        buckets = diagnostic_buckets(diagnostics)
        limited_diagnostics = (
            diagnostics
            if message_limit is None
            else limit_diagnostics(diagnostics, message_limit)
        )
        limited_buckets = diagnostic_buckets(limited_diagnostics)
        omitted_errors = len(buckets["errors"]) - len(limited_buckets["errors"])
        omitted_warnings = len(buckets["warnings"]) - len(limited_buckets["warnings"])
        omitted_infos = len(buckets["infos"]) - len(limited_buckets["infos"])
        omitted_counts = diagnostic_counts(
            errors=omitted_errors,
            warnings=omitted_warnings,
            infos=omitted_infos,
        )
        has_omissions = bool(omitted_counts)
        timeout_context = (
            self.timeout_context.render(options)
            if self.status == "timeout"
            and self.timeout_context is not None
            and options.include_timeout_context
            else None
        )

        return pruned_dict(
            {
                "status": self.status,
                "file": display_file(self.file, self.workspace),
                "counts": diagnostic_counts(
                    errors=len(buckets["errors"]),
                    warnings=len(buckets["warnings"]),
                    infos=len(buckets["infos"]),
                ),
                "omitted_counts": omitted_counts,
                "message_limit": message_limit if has_omissions else None,
                "diagnostics": [
                    diagnostic.render(options) for diagnostic in limited_diagnostics
                ],
                "timed_out": True if self.timed_out else None,
                "restarted": True if self.restarted else None,
                "timeout_context": timeout_context,
                "long_running_commands": [
                    command.render(options) for command in self.long_running_commands
                ],
                "message": self.message,
            }
        )


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


def location_string(
    range_: Any,
    location_format: LocationFormat = "range",
) -> str | None:
    """Render an LSP range as a compact human-facing 1-based location."""
    if not isinstance(range_, dict):
        return None
    start = range_start(range_)
    end = range_end(range_)
    if start is None or end is None:
        return None
    if location_format == "point":
        return position_string(start)
    return f"{position_string(start)} to {position_string(end)}"


def position_string(position: Any) -> str | None:
    position_ = _position_dict(position)
    if position_ is None:
        return None
    return f"{position_['line'] + 1}:{position_['character'] + 1}"


def range_start(range_: Any) -> dict[str, int] | None:
    if not isinstance(range_, dict):
        return None
    return _position_dict(range_.get("start"))


def range_end(range_: Any) -> dict[str, int] | None:
    if not isinstance(range_, dict):
        return None
    return _position_dict(range_.get("end"))


def diagnostic_level(severity: int | None) -> str | None:
    if severity == 1:
        return "error"
    if severity == 2:
        return "warning"
    if severity in (3, 4):
        return "info"
    return None


def run_at_location(line: int, character: int) -> str:
    return f"{line}:{character}"


def diagnostic_counts(*, errors: int, warnings: int, infos: int) -> dict[str, int]:
    counts = {
        "errors": errors,
        "warnings": warnings,
        "infos": infos,
    }
    return {key: count for key, count in counts.items() if count > 0}


def display_file(file: str, workspace: str | None) -> str:
    path = Path(file)
    if workspace is not None:
        try:
            return path.resolve().relative_to(Path(workspace).resolve()).as_posix()
        except ValueError:
            pass
    return file


def long_running_commands_from_perf_data(
    *,
    content: str,
    perf_data: Any,
    threshold_seconds: float,
    executed_ranges: list[dict[str, Any]] | None = None,
    limit: int = MAX_LONG_RUNNING_COMMANDS,
) -> list[LongRunningCommand]:
    raw = lsp_to_jsonable(perf_data)
    if not isinstance(raw, dict) or threshold_seconds <= 0 or limit <= 0:
        return []

    timings = raw.get("timings")
    if not isinstance(timings, list):
        return []

    executed_range_keys = (
        {_range_key(range_) for range_ in executed_ranges}
        if executed_ranges
        else None
    )
    commands: list[LongRunningCommand] = []
    for timing in timings:
        if not isinstance(timing, dict):
            continue
        info = timing.get("info")
        range_ = timing.get("range")
        if not isinstance(info, dict) or not isinstance(range_, dict):
            continue
        if (
            executed_range_keys is not None
            and _range_key(range_) not in executed_range_keys
        ):
            continue
        if info.get("cache_hit") is True:
            continue
        time_seconds = info.get("time")
        if not isinstance(time_seconds, (int, float)):
            continue
        if float(time_seconds) < threshold_seconds:
            continue
        text = _source_text_for_range(content, range_)
        if not text:
            continue
        commands.append(
            LongRunningCommand(
                range=range_,
                text=text,
                time_seconds=float(time_seconds),
            )
        )
        if len(commands) >= limit:
            break
    return commands


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


def _render_diagnostics(
    diagnostics: list[Diagnostic],
    options: RenderOptions,
) -> list[Diagnostic]:
    diagnostics = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic_matches_severity(diagnostic, options.severity)
    ]

    if not options.deduplicate_proof_errors:
        return diagnostics

    seen_error_proofs: set[str] = set()
    # A failed top-level sentence can make later proof commands surface as
    # proof_name-less errors; keep only the first such error in rendered output.
    seen_global_error = False
    filtered: list[Diagnostic] = []
    for diagnostic in diagnostics:
        if diagnostic.severity == 1:
            if diagnostic.proof_name is None:
                if seen_global_error:
                    continue
                seen_global_error = True
            elif diagnostic.proof_name in seen_error_proofs:
                continue
            else:
                seen_error_proofs.add(diagnostic.proof_name)
        filtered.append(diagnostic)
    return filtered


def diagnostic_matches_severity(
    diagnostic: Diagnostic,
    minimum_severity: DiagnosticSeverity,
) -> bool:
    level = diagnostic_level(diagnostic.severity) or "info"
    return (
        DIAGNOSTIC_LEVEL_PRIORITY[level]
        >= DIAGNOSTIC_LEVEL_PRIORITY[minimum_severity]
    )


def _completed_only(document: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(document, dict):
        return None
    completed = document.get("completed")
    return completed if isinstance(completed, dict) else None


def _latest_processing_range(progress: Any) -> dict[str, Any] | None:
    if not isinstance(progress, dict):
        return None
    processing = progress.get("processing")
    if not isinstance(processing, list):
        return None
    for item in processing:
        if not isinstance(item, dict):
            continue
        range_ = item.get("range")
        if isinstance(range_, dict):
            return range_
    return None


def _source_preview_for_range(
    content: str, range_: dict[str, Any]
) -> dict[str, Any] | None:
    return _next_unchecked_preview(content, range_start(range_))


def _source_text_for_range(content: str, range_: dict[str, Any]) -> str | None:
    start = range_start(range_)
    end = range_end(range_)
    if start is None or end is None:
        return None

    lines = content.splitlines(keepends=True)
    start_line = start["line"]
    end_line = end["line"]
    if start_line < 0 or end_line < start_line or start_line >= len(lines):
        return None
    if end_line >= len(lines):
        end_line = len(lines) - 1

    if start_line == end_line:
        text = lines[start_line][start["character"] : end["character"]]
    else:
        parts = [lines[start_line][start["character"] :]]
        parts.extend(lines[start_line + 1 : end_line])
        parts.append(lines[end_line][: end["character"]])
        text = "".join(parts)
    return _truncate_preview_line(" ".join(text.strip().split()), limit=240)


def _next_unchecked_preview(
    content: str, position: dict[str, int] | None
) -> dict[str, Any] | None:
    if position is None:
        return None

    line_number = position["line"]
    character = position["character"]
    lines = content.splitlines()
    for current_line_number in range(line_number, len(lines)):
        line = lines[current_line_number]
        start = character if current_line_number == line_number else 0
        start = max(0, min(start, len(line)))
        for index in range(start, len(line)):
            if not line[index].isspace():
                return {
                    "location": f"{current_line_number + 1}:{index + 1}",
                    "line": _truncate_preview_line(line.strip()),
                }
    return None


def _truncate_preview_line(line: str, limit: int = 160) -> str:
    if len(line) <= limit:
        return line
    return f"{line[: limit - 3]}..."


def _position_dict(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    line = value.get("line")
    character = value.get("character")
    if isinstance(line, int) and isinstance(character, int):
        return {"line": line, "character": character}
    return None


def _range_key(range_: dict[str, Any]) -> str:
    start = range_start(range_)
    end = range_end(range_)
    return f"{start}:{end}"


def _feedback_entries(feedback: Any) -> list[Any]:
    raw = lsp_to_jsonable(feedback)
    if isinstance(raw, dict):
        entries = raw.get("feedback")
        return entries if isinstance(entries, list) else []
    if isinstance(raw, list):
        return raw
    return []
