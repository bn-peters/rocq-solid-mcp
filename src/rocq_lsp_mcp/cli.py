from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from typing import TextIO

from .manager import RocqLspManager
from .models import RenderOptions


def main(argv: Sequence[str] | None = None) -> int | None:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        run_mcp_server()
        return None

    parsed = _parser().parse_args(args)
    if parsed.command == "check":
        return asyncio.run(_check(parsed.file, sys.stdout))
    return 2


def run_mcp_server() -> None:
    from .server import main as server_main

    server_main()


async def _check(file: str, output: TextIO) -> int:
    manager = RocqLspManager()
    try:
        result = await manager.check_file(
            file,
            command_timeout_seconds=0,
            overall_timeout_seconds=0,
            long_running_seconds=0,
        )
        rendered = result.render(RenderOptions(message_limit=None))
        print(json.dumps(rendered, indent=2), file=output)
        return 0
    finally:
        await manager.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bens-rocq-mcp")
    subcommands = parser.add_subparsers(dest="command", required=True)

    check = subcommands.add_parser(
        "check",
        help="check a Rocq file and print formatted JSON",
    )
    check.add_argument("file", help="path to a .v file")

    return parser


if __name__ == "__main__":
    raise SystemExit(main())
