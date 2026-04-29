from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from rocq_lsp_mcp.config import RocqLspConfig
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
            timeout=30,
            hard_timeout_grace=5,
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
            timeout=30,
            hard_timeout_grace=5,
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
            timeout=30,
            hard_timeout_grace=5,
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
        _expected_tactic_failure(source.as_uri()),
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


def _range(start_character: int, end_character: int) -> dict[str, object]:
    return {
        "start": {"line": 0, "character": start_character},
        "end": {"line": 0, "character": end_character},
    }


def _expected_tactic_failure(uri: str) -> dict[str, object]:
    range_ = _range(19, 24)
    return {
        "range": range_,
        "message": "Tactic failure.",
        "severity": 1,
        "code": None,
        "source": None,
        "data": {"sentenceRange": range_, "proofName": "Unnamed_thm"},
        "proof_name": "Unnamed_thm",
        "proof_state": {
            "textDocument": {"uri": uri, "version": 1},
            "position": {"line": 0, "character": 19},
            "range": range_,
            "goals": {
                "goals": [
                    {
                        "name": None,
                        "hyps": [],
                        "ty": "0 = 0",
                    }
                ],
                "stack": [],
                "bullet": None,
                "shelf": [],
                "given_up": [],
            },
            "program": [],
            "messages": [],
            "error": "Tactic failure.",
        },
        "raw": {
            "range": range_,
            "severity": 1,
            "message": "Tactic failure.",
            "data": {"sentenceRange": range_, "proofName": "Unnamed_thm"},
        },
    }
