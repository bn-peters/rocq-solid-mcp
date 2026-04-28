from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--promote",
        action="store_true",
        default=False,
        help="rewrite promoted e2e expected transcripts",
    )
