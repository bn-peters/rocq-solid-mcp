from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.server.lifespan import lifespan
from pydantic import Field

from .manager import RocqLspManager
from .models import DEFAULT_MESSAGE_LIMIT, CompileResult


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
    file: str,
    ctx: Context,
    message_limit: Annotated[
        int,
        Field(
            description=(
                "Returned diagnostics per severity bucket; values above 50 "
                "are capped."
            ),
        ),
    ] = DEFAULT_MESSAGE_LIMIT,
    command_timeout_seconds: Annotated[
        float | None,
        Field(
            description=(
                "Optional per Rocq sentence timeout in seconds. None uses the "
                "server default; 0 disables it for this call."
            ),
        ),
    ] = None,
) -> CompileResult:
    """Compile a Rocq file through a cached rocq-lsp session.

    message_limit caps returned diagnostics per severity. The default is 10,
    and values above the hard limit of 50 are capped.

    command_timeout_seconds optionally cancels a compile when one Rocq sentence
    runs longer than the requested number of seconds. A value of 0 disables the
    per-command timeout for this call.
    """
    manager: RocqLspManager = ctx.request_context.lifespan_context
    return await manager.compile_file(
        file,
        message_limit=message_limit,
        command_timeout_seconds=command_timeout_seconds,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
