from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.server.lifespan import lifespan
from pydantic import Field

from .config import bounded_overall_timeout_seconds
from .manager import RocqLspManager
from .models import RenderOptions


@lifespan
async def app_lifespan(server: Any) -> Any:
    manager = RocqLspManager()
    manager.start_idle_cleanup()
    try:
        yield manager
    finally:
        await manager.close()


mcp = FastMCP("bens-rocq-mcp", lifespan=app_lifespan)


@mcp.tool()
async def rocq_check_file(
    ctx: Context,
    file: Annotated[
        str,
        Field(
            description="Absolute path to a .v file.",
        ),
    ],
    command_timeout_seconds: Annotated[
        float | None,
        Field(
            description=(
                "Per Rocq sentence watchdog for checking. None uses the server "
                "default, 0 disables it, and positive values cancel a slow sentence."
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

    Use this to check files for errors. This tool reports only error
    diagnostics. (To find warnings, recompile the file manually.) Error
    diagnostics include the proof state at the error position and precise
    ranges.

    The first error in the file is always reported, and usually later errors as
    well. If there are many errors, some might be omitted. The tool also
    reports Rocq sentences taking a long time.

    This tool is efficient due to caching. It creates no build artifacts, and
    does not recompile upstream dependencies itself. Use `restart = true` to
    clear the cache when upstream dependencies have been recompiled. Timeouts
    are handled gracefully, and partial diagnostics are returned when possible.
    Repeated checks after timeouts use the cache.
    """
    manager: RocqLspManager = ctx.request_context.lifespan_context
    result = await manager.check_file(
        file,
        command_timeout_seconds=command_timeout_seconds,
        long_running_seconds=_long_running_alert_threshold(
            manager.config.timeout_policy.request_seconds
        ),
        restart=restart,
    )
    return result.render(RenderOptions(message_limit=manager.config.message_limit))


@mcp.tool()
async def rocq_run_at(
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
) -> dict[str, Any]:
    """Run Rocq commands at a file position and return their output.

    Use this for one-off Rocq tasks such as Check, Search, Locate, Require, or
    other one-off debugging asks.

    Prefer calling this tool right after imports. If local definitions, scopes,
    or modules are needed, call it between lemmas/definitions.
    Repeated calls after timeouts use the cache.
    """
    manager: RocqLspManager = ctx.request_context.lifespan_context
    result = await manager.run_at(
        file,
        commands,
        line,
        character,
    )
    return result.render()


def _long_running_alert_threshold(request_timeout_seconds: float | None) -> float:
    return bounded_overall_timeout_seconds(request_timeout_seconds) * 0.8


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
