from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.server.lifespan import lifespan
from pydantic import Field

from .config import DEFAULT_OVERALL_TIMEOUT_SECONDS, MAX_OVERALL_TIMEOUT_SECONDS
from .manager import RocqLspManager
from .models import (
    DEFAULT_DIAGNOSTIC_SEVERITY,
    DEFAULT_LONG_RUNNING_SECONDS,
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
                "Soft coq-lsp request deadline, in seconds. Default 55s,"
                "maximum 3600s."
            ),
        ),
    ] = DEFAULT_OVERALL_TIMEOUT_SECONDS,
    command_timeout_seconds: Annotated[
        float | None,
        Field(
            description=(
                "Per Rocq sentence execution timeout. Default 5s, 0 disables."
            ),
        ),
    ] = None,
    # TODO remove this feature instead, alert at 80% of the overall timeout
    long_running_seconds: Annotated[
        float,
        Field(
            ge=0,
            description=(
                "Optional threshold, in seconds, alerting on long-running commands."
                "Default 3s, 0 disables."
            ),
        ),
    ] = DEFAULT_LONG_RUNNING_SECONDS,
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
        long_running_seconds=long_running_seconds,
        restart=restart,
    )
    return result.render(RenderOptions(message_limit=message_limit, severity=severity))


@mcp.tool()
async def rocq_lsp_run_command(
    file: Annotated[str, Field(description="Absolute path to a .v file.")],
    # TODO generalize this tool: accept a list of commands, instead of a single one, all of which are executed at the given position.
    command: Annotated[
        str,
        Field(
            description=(
                "A single Rocq command to evaluate, e.g. 'Check Nat.add.' or "
                "'Search (_ + _ = _).'."
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
                "Soft coq-lsp request deadline, in seconds. Default 60s, maximum "
                "3600s."
            ),
        ),
    ] = DEFAULT_OVERALL_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run one Rocq command at a file position and return its output.

    Use this to query information about the proof environment, for instance using Search, Check, or Locate.

    Prefer calling this right after imports. If local definitions, scopes, or
    modules are needed, call it between lemmas/definitions.

    overall_timeout_seconds is a soft deadline for reporting partial results. It should be lower than the host's tool call timeout. 
    """
    manager: RocqLspManager = ctx.request_context.lifespan_context
    result = await manager.run_command(
        file,
        command,
        line,
        character,
        overall_timeout_seconds=overall_timeout_seconds,
    )
    return result.render()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
