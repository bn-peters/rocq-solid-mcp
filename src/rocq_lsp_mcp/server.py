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
async def rocq_lsp_check_file(
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
                "Timeout at which partial diagnostics are returned. Default 60s, maximum 3600s."
            ),
        ),
    ] = DEFAULT_OVERALL_TIMEOUT_SECONDS,
    command_timeout_seconds: Annotated[
        float | None,
        Field(
            description=(
                "Maximal per Rocq sentence compile time. Default "
                "5s, 0 disables."
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
    """Check a Rocq file and report diagnostics.

    Use this to check files for errors. The diagnostics include the proof state
    at error position and precise ranges.

    The first error in the file is always reported, and usually later errors as
    well. The tool also reports Rocq sentences taking a long time.

    This check is efficient due to caching. It creates no build artifacts, and
    does not recompile upstream dependencies itself. Use `restart = true` to
    clear the cache when upstream dependencies have been recompiled.

    overall_timeout_seconds is a soft deadline for returning partial diagnostics
    before the MCP host cancels the tool call. It should be lower than the
    host's tool call timeout. Repeated checks after timeouts use the cache.
    """
    manager: RocqLspManager = ctx.request_context.lifespan_context
    result = await manager.check_file(
        file,
        overall_timeout_seconds=overall_timeout_seconds,
        command_timeout_seconds=command_timeout_seconds,
        long_running_seconds=_long_running_alert_threshold(overall_timeout_seconds),
        restart=restart,
    )
    return result.render(
        RenderOptions(message_limit=message_limit, severity=severity)
    )


@mcp.tool()
async def rocq_lsp_run_at(
    file: Annotated[str, Field(description="Absolute path to a .v file.")],
    commands: Annotated[
        str,
        Field(
            description=(
                "One string containing one or more complete Rocq commands to run at "
                "the same file position, e.g. 'Require Import List. Check list.'"
            ),
        ),
    ],
    line: Annotated[
        int,
        Field(
            description=(
                "1-based line number in the file at which the commands "
                "string is ran."
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
                "Soft coq-lsp request deadline for partial run_at output before "
                "the MCP host cancels, in seconds. Default 60s, maximum 3600s."
            ),
        ),
    ] = DEFAULT_OVERALL_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run Rocq commands at a file position and return their output.

    Use this for one-off Rocq tasks such as Check, Search, Locate, Require, or
    other one-off debuggint asks.

    Prefer calling this right after imports. If local definitions, scopes, or
    modules are needed, call it between lemmas/definitions.

    overall_timeout_seconds is a soft deadline for reporting partial results.
    It should be lower than the host's tool call timeout.
    """
    manager: RocqLspManager = ctx.request_context.lifespan_context
    result = await manager.run_at(
        file,
        commands,
        line,
        character,
        overall_timeout_seconds=overall_timeout_seconds,
    )
    return result.render()


def _long_running_alert_threshold(overall_timeout_seconds: float) -> float:
    return min(float(overall_timeout_seconds), MAX_OVERALL_TIMEOUT_SECONDS) * 0.8


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
