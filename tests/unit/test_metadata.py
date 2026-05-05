from __future__ import annotations

import tomllib
from pathlib import Path


def test_project_name_and_console_script_are_bens_rocq_mcp() -> None:
    pyproject = Path(__file__).parents[2] / "pyproject.toml"
    metadata = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    assert metadata["project"]["name"] == "bens-rocq-mcp"
    assert metadata["project"]["scripts"] == {
        "bens-rocq-mcp": "rocq_lsp_mcp.cli:main",
    }
