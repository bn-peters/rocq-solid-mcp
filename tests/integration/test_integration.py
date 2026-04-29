from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from rocq_lsp_mcp.config import RocqLspConfig, TimeoutPolicy
from rocq_lsp_mcp.manager import RocqLspManager


pytestmark = pytest.mark.skipif(
    shutil.which("coq-lsp") is None,
    reason="install coq-lsp to run integration tests",
)


@pytest.mark.asyncio
async def test_valid_file_compiles_with_zero_errors(tmp_path: Path) -> None:
    source = tmp_path / "Ok.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    manager = RocqLspManager(
        RocqLspConfig(
            binary="coq-lsp",
            timeout_policy=TimeoutPolicy(
                request_seconds=30,
                cancel_grace_seconds=5,
            ),
            max_errors=1000000,
            workspace=tmp_path,
        )
    )
    try:
        result = await manager.compile_file(str(source))
    finally:
        await manager.close()

    assert result.status == "complete"
    assert result.error_count == 0


@pytest.mark.asyncio
async def test_missing_variable_names_report_multiple_errors(tmp_path: Path) -> None:
    source = tmp_path / "Bad.v"
    source.write_text(
        "Check does_not_exist_1.\nCheck does_not_exist_2.\n",
        encoding="utf-8",
    )
    manager = RocqLspManager(
        RocqLspConfig(
            binary="coq-lsp",
            timeout_policy=TimeoutPolicy(
                request_seconds=30,
                cancel_grace_seconds=5,
            ),
            max_errors=1000000,
            workspace=tmp_path,
        )
    )
    try:
        result = await manager.compile_file(str(source))
    finally:
        await manager.close()

    assert result.status == "complete"
    assert result.error_count >= 2


@pytest.mark.asyncio
async def test_file_change_rechecks_and_reports_failure_proof_state(
    tmp_path: Path,
) -> None:
    source = tmp_path / "ChangingGoal.v"
    source.write_text("Goal 0 = 0. Proof. fail. Qed.\n", encoding="utf-8")
    manager = RocqLspManager(
        RocqLspConfig(
            binary="coq-lsp",
            timeout_policy=TimeoutPolicy(
                request_seconds=30,
                cancel_grace_seconds=5,
            ),
            max_errors=1000000,
            workspace=tmp_path,
        )
    )
    try:
        failing = await manager.compile_file(str(source))

        source.write_text(
            "Goal 0 = 0. Proof. reflexivity. Qed.\n",
            encoding="utf-8",
        )
        fixed = await manager.compile_file(str(source))
    finally:
        await manager.close()

    assert failing.status == "complete"
    assert failing.version == 1
    assert failing.completed == {
        "status": ["Yes"],
        "range": {
            "start": {"line": 1, "character": 0},
            "end": {"line": 1, "character": 0},
        },
    }
    assert failing.error_count == 1
    assert failing.warning_count == 0
    assert failing.info_count == 0
    assert "Pp_" not in json.dumps(failing.model_dump())
    assert [error.model_dump() for error in failing.errors] == [
        _expected_tactic_failure(),
    ]

    assert fixed.status == "complete"
    assert fixed.version == 2
    assert fixed.completed == {
        "status": ["Yes"],
        "range": {
            "start": {"line": 1, "character": 0},
            "end": {"line": 1, "character": 0},
        },
    }
    assert fixed.error_count == 0
    assert fixed.warning_count == 0
    assert fixed.info_count == 0
    assert fixed.diagnostics == []
    assert fixed.errors == []


def _expected_tactic_failure() -> dict[str, object]:
    return {
        "level": "error",
        "message": "Tactic failure.",
        "location": "1:20 to 1:25",
        "proof_name": "Unnamed_thm",
        "proof_state": {"goals": [{"conclusion": "0 = 0"}]},
    }
