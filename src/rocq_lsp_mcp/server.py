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
                "default, 0 disables it, and positive values abort on slow sentences."
            ),
        ),
    ] = None,
    timeout_after_error_seconds: Annotated[
        float | None,
        Field(
            description=(
                "Overall deadline once the first error has been reported: "
                "the whole check is stopped at most this many seconds after "
                "the first error. None uses the server default, 0 disables "
                "it, and positive values enable it."
            ),
        ),
    ] = None,
    clear_cache: Annotated[
        bool,
        Field(
            description=(
                "Restart this file's coq-lsp process before checking."
            ),
        ),
    ] = False,
) -> dict[str, Any]:
    """Check a Rocq file and report diagnostics.

    Use this to check files for errors. (To find warnings, recompile the file
    manually.) Error diagnostics include the source sentence and the proof state
    at error positions.

    The first error in a file is always reported, along with later errors
    reached within the after-error deadline. The list of errors may be
    truncated. The tool also reports Rocq sentences running for long time.

    This tool is efficient due to per-file caching. For dune-based Rocq projects,
    it attempts to rebuild dependencies before checking and restarts the cached
    coq-lsp process when that applies. Use `clear_cache = true` when project
    files have changed or dependencies were rebuilt outside dune.

    Timeouts are handled gracefully, and partial diagnostics are returned when
    possible. Repeated checks after timeouts use the cache.
    """
    manager: RocqLspManager = ctx.request_context.lifespan_context
    result = await manager.check_file(
        file,
        command_timeout_seconds=command_timeout_seconds,
        timeout_after_error_seconds=timeout_after_error_seconds,
        long_running_seconds=_long_running_alert_threshold(
            manager.config.timeout_policy.request_seconds
        ),
        clear_cache=clear_cache,
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

    Use this for running Rocq commands, such as:
    - Search Nat.add.
    - Search insert delete "_eq".
    - Check (3 + 4).
    - About Nat.add.
    - Locate "+".
    - Show. (to show the proof state at a position)
    The tool is also useful for one-off debugging tasks.

    Prefer calling this tool right after imports or between lemmas/definitions.
    Only use within proofs when necessary.
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
