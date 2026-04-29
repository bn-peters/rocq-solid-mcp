from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.server.lifespan import lifespan
from pydantic import Field

from .config import DEFAULT_OVERALL_TIMEOUT_SECONDS, MAX_OVERALL_TIMEOUT_SECONDS
from .manager import RocqLspManager
from .models import DEFAULT_MESSAGE_LIMIT, CommandResult, CompileResult


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
                "Optional maximum returned diagnostics per category "
                "(error, warning, info). Default 10, maximum 50."
            ),
        ),
    ] = DEFAULT_MESSAGE_LIMIT,
    overall_timeout_seconds: Annotated[
        float,
        Field(
            gt=0,
            le=MAX_OVERALL_TIMEOUT_SECONDS,
            description=(
                "Optional overall coq-lsp request timeout, in seconds. "
                "Default 60s, maximum 3600s."
            ),
        ),
    ] = DEFAULT_OVERALL_TIMEOUT_SECONDS,
    command_timeout_seconds: Annotated[
        float | None,
        Field(
            description=(
                "Optional per Rocq sentence timeout, in seconds. Default 5s, 0 disables"
            ),
        ),
    ] = None,
) -> CompileResult:
    """Compile a Rocq file and report diagnostics.

    Use this to check to check files for errors. The diagnostics are much faster and much more detailed than those of regular compilation. They include the current proof state at the error position and precise ranges.

    Adjust the request timeout only when a file is known to take a long time to compile. Adjust the command timeout only when specific tactics in a file are known to be slow. The message limit should only rarely be adjusted.
    """
    manager: RocqLspManager = ctx.request_context.lifespan_context
    return await manager.compile_file(
        file,
        message_limit=message_limit,
        overall_timeout_seconds=overall_timeout_seconds,
        command_timeout_seconds=command_timeout_seconds,
    )


@mcp.tool()
async def rocq_lsp_run_command(
    file: Annotated[str, Field(description="Absolute path to a .v file.")],
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
                "Optional overall coq-lsp request timeout, in seconds. "
                "Default 60s, maximum 3600s."
            ),
        ),
    ] = DEFAULT_OVERALL_TIMEOUT_SECONDS,
) -> CommandResult:
    """Run one Rocq command at a file position and return its output.

    Use this to query information about the proof environment, for instance using Search, Check, or Locate.

    Prefer calling this right after imports. If local definitions, scopes, or
    modules are needed, call it between lemmas or definitions.

    overall_timeout_seconds is the outer coq-lsp request timeout. It defaults to
    60 seconds and may be at most 3600 seconds.
    """
    manager: RocqLspManager = ctx.request_context.lifespan_context
    return await manager.run_command(
        file,
        command,
        line,
        character,
        overall_timeout_seconds=overall_timeout_seconds,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
