from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from harness import run_case, scenario_cases

pytestmark = pytest.mark.skipif(
    shutil.which("coq-lsp") is None,
    reason="install coq-lsp to run e2e tests",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("case_dir", scenario_cases(), ids=lambda path: path.name)
async def test_e2e_scenario(
    case_dir: Path,
    tmp_path: Path,
    pytestconfig: pytest.Config,
) -> None:
    await run_case(
        case_dir,
        tmp_path,
        promote=bool(pytestconfig.getoption("--promote")),
    )
