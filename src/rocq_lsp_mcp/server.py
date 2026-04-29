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
) -> CompileResult:
    """Compile a Rocq file through a cached rocq-lsp session.

    message_limit caps returned diagnostics per severity. The default is 10,
    and values above the hard limit of 50 are capped.
    """
    manager: RocqLspManager = ctx.request_context.lifespan_context
    return await manager.compile_file(file, message_limit=message_limit)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
