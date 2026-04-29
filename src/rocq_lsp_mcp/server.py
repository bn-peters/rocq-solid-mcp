from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.server.lifespan import lifespan

from .manager import RocqLspManager
from .models import CommandResult, CompileResult


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
async def rocq_lsp_compile_file(file: str, ctx: Context) -> CompileResult:
    """Compile a Rocq file through a cached rocq-lsp session."""
    manager: RocqLspManager = ctx.request_context.lifespan_context
    return await manager.compile_file(file)


@mcp.tool()
async def rocq_lsp_run_command(
    file: str, command: str, line: int, ctx: Context, character: int = 1
) -> CommandResult:
    """Run one Rocq command at a file position and return its output.

    Prefer calling this right after imports. If local definitions, scopes, or
    modules are needed, call it between lemmas or definitions.
    """
    manager: RocqLspManager = ctx.request_context.lifespan_context
    return await manager.run_command(file, command, line, character)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
