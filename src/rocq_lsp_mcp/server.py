from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.server.lifespan import lifespan
from pydantic import Field

from .config import DEFAULT_OVERALL_TIMEOUT_SECONDS, MAX_OVERALL_TIMEOUT_SECONDS
from .manager import RocqLspManager
from .models import (
    DEFAULT_DIAGNOSTIC_SEVERITY,
    DEFAULT_MESSAGE_LIMIT,
    DiagnosticSeverity,
    RenderOptions,
)


@lifespan
async def app_lifespan(server: Any) -> Any:
    manager = RocqLspManager()
    manager.start_idle_cleanup()
    try:
        yield manager
    finally:
        await manager.close()


mcp = FastMCP("rocq-lsp-mcp", lifespan=app_lifespan)


@mcp.tool()
async def rocq_lsp_compile_file(
    ctx: Context,
    file: Annotated[
        str,
        Field(
            description="Absolute path to a .v file.",
        ),
    ],
    message_limit: Annotated[
        int,
        Field(
            description=(
                "Maximum returned diagnostics per category "
                "(error, warning, info). Default 10, maximum 50."
            ),
        ),
    ] = DEFAULT_MESSAGE_LIMIT,
    severity: Annotated[
        DiagnosticSeverity,
        Field(
            description=(
                "Minimum diagnostic severity to return: "
                "info, warning, or error. Default info."
            ),
        ),
    ] = DEFAULT_DIAGNOSTIC_SEVERITY,
    overall_timeout_seconds: Annotated[
        float,
        Field(
            gt=0,
            le=MAX_OVERALL_TIMEOUT_SECONDS,
            description=(
                "Soft coq-lsp request deadline for partial diagnostics before the "
                "MCP host cancels, in seconds. Default 60s, maximum 3600s."
            ),
        ),
    ] = DEFAULT_OVERALL_TIMEOUT_SECONDS,
    command_timeout_seconds: Annotated[
        float | None,
        Field(
            description=(
                "Optional per Rocq sentence watchdog for compile requests. Default "
                "5s, 0 disables; a slow sentence returns timeout context."
            ),
        ),
    ] = None,
    restart: Annotated[
        bool,
        Field(
            description=(
                "Notify coq-lsp that compiled dependencies may have changed before "
                "checking this file."
            ),
        ),
    ] = False,
) -> dict[str, Any]:
    """Compile a Rocq file and report diagnostics.

    Use this to check to check files for errors. The diagnostics are much more detailed than those of regular compilation. They include the current proof state at the error position and precise ranges.

    This file reports at least the first error in the file, and usually more. It also reports tactics and commands taking a long time.

    This command is very efficient due to caching. It creates no compilation artifacts. Use `restart = true` to clear the cache when upstream dependencies have been recompiled.

    overall_timeout_seconds is a soft deadline for returning partial diagnostics before the MCP host cancels the tool call. It should be lower than the host's tool call timeout. Repeated compilation after timeouts respects the cache.
    """
    manager: RocqLspManager = ctx.request_context.lifespan_context
    result = await manager.compile_file(
        file,
        overall_timeout_seconds=overall_timeout_seconds,
        command_timeout_seconds=command_timeout_seconds,
        long_running_seconds=_long_running_alert_threshold(overall_timeout_seconds),
        restart=restart,
    )
    return result.render(RenderOptions(message_limit=message_limit, severity=severity))


@mcp.tool()
async def rocq_lsp_run_command(
    file: Annotated[str, Field(description="Absolute path to a .v file.")],
    commands: Annotated[
        list[str],
        Field(
            description=(
                "One or more complete Rocq commands to evaluate at the same file "
                "position, e.g. ['Check Nat.add.', 'Search (_ + _ = _).']."
            ),
        ),
    ],
    line: Annotated[
        int,
        Field(
            description=(
                "1-based line number in the file before which the command is injected."
            ),
        ),
    ],
    ctx: Context,
    character: Annotated[
        int,
        Field(description="1-based column offset. Defaults to 1 (start of line)."),
    ] = 1,
    overall_timeout_seconds: Annotated[
        float,
        Field(
            gt=0,
            le=MAX_OVERALL_TIMEOUT_SECONDS,
            description=(
                "Soft coq-lsp request deadline for partial command output before "
                "the MCP host cancels, in seconds. Default 60s, maximum 3600s."
            ),
        ),
    ] = DEFAULT_OVERALL_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run Rocq commands at a file position and return their output.

    Use this to query information about the proof environment, for instance using Search, Check, or Locate.

    Prefer calling this right after imports. If local definitions, scopes, or
    modules are needed, call it between lemmas/definitions.

    overall_timeout_seconds is a soft deadline for reporting partial results. It should be lower than the host's tool call timeout. 
    """
    if not commands:
        return {
            "status": "failed",
            "file": file,
            "message": "commands must contain at least one Rocq command ending in '.'",
        }

    manager: RocqLspManager = ctx.request_context.lifespan_context
    results = [
        await manager.run_command(
            file,
            command,
            line,
            character,
            overall_timeout_seconds=overall_timeout_seconds,
        )
        for command in commands
    ]
    rendered_results = [result.render() for result in results]
    timed_out = any(result.timed_out for result in results)
    restarted = any(result.restarted for result in results)
    response: dict[str, Any] = {
        "status": _batch_status(rendered_results),
        "file": rendered_results[0].get("file", file),
        "results": rendered_results,
    }
    if timed_out:
        response["timed_out"] = True
    if restarted:
        response["restarted"] = True
    if location := rendered_results[0].get("location"):
        response["location"] = location
    return response


def _long_running_alert_threshold(overall_timeout_seconds: float) -> float:
    return min(float(overall_timeout_seconds), MAX_OVERALL_TIMEOUT_SECONDS) * 0.8


def _batch_status(results: list[dict[str, Any]]) -> str:
    statuses = {result.get("status") for result in results}
    if "timeout" in statuses:
        return "timeout"
    if "failed" in statuses:
        return "failed"
    return "complete"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
