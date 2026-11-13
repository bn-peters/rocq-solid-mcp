from __future__ import annotations

import tomllib
from pathlib import Path


def test_project_name_and_console_scripts() -> None:
    pyproject = Path(__file__).parents[2] / "pyproject.toml"
    metadata = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    assert metadata["project"]["name"] == "rocq-solid-mcp"
    assert metadata["project"]["license"] == "MIT"
    assert metadata["project"]["scripts"] == {
        "rocq-solid-mcp": "rocq_solid_mcp.cli:main",
        "bens-rocq-mcp": "rocq_solid_mcp.cli:main",
    }
