from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from rocq_solid_mcp.config import RocqLspConfig, TimeoutPolicy
from rocq_solid_mcp.manager import RocqLspManager


pytestmark = pytest.mark.skipif(
    shutil.which("opam") is None or shutil.which("coq-lsp") is None,
    reason="install opam and coq-lsp to run integration tests",
)


@pytest.mark.asyncio
async def test_valid_file_checks_with_zero_errors(tmp_path: Path) -> None:
    source = tmp_path / "Ok.v"
    source.write_text("Check nat.\n", encoding="utf-8")
    manager = RocqLspManager(
        RocqLspConfig(
            binary="coq-lsp",
            timeout_policy=TimeoutPolicy(
                request_seconds=30,
                cancel_grace_seconds=5,
                request_seconds_after_error=None,
            ),
            max_errors=1000000,
            workspace=tmp_path,
        )
    )
    try:
        result = await manager.check_file(str(source))
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
                request_seconds_after_error=None,
            ),
            max_errors=1000000,
            workspace=tmp_path,
        )
    )
    try:
        result = await manager.check_file(str(source))
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
                request_seconds_after_error=None,
            ),
            max_errors=1000000,
            workspace=tmp_path,
        )
    )
    try:
        failing = await manager.check_file(str(source))

        source.write_text(
            "Goal 0 = 0. Proof. reflexivity. Qed.\n",
            encoding="utf-8",
        )
        fixed = await manager.check_file(str(source))
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
    assert failing.error_count == 2
    assert failing.warning_count == 0
    assert failing.info_count == 0
    rendered_failing = failing.render()
    assert "Pp_" not in json.dumps(rendered_failing)
    assert rendered_failing["diagnostics"] == [
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


@pytest.mark.asyncio
async def test_run_at_search_returns_feedback(tmp_path: Path) -> None:
    import_line = "From Stdlib Require Import Arith.PeanoNat."
    source = tmp_path / "Search.v"
    source.write_text(f"{import_line}\n", encoding="utf-8")
    manager = RocqLspManager(
        RocqLspConfig(
            binary="coq-lsp",
            timeout_policy=TimeoutPolicy(
                request_seconds=30,
                cancel_grace_seconds=5,
                request_seconds_after_error=None,
            ),
            max_errors=1000000,
            workspace=tmp_path,
        )
    )
    try:
        result = await manager.run_at(
            str(source),
            "Search (_ + 0 = _).",
            line=1,
            character=len(import_line) + 1,
        )
    finally:
        await manager.close()

    assert result.status == "complete"
    assert any(
        "Nat.add_0_r: forall n : nat, n + 0 = n" in item.text
        for item in result.output
    )


@pytest.mark.asyncio
async def test_run_at_reports_warning_feedback(tmp_path: Path) -> None:
    source = tmp_path / "WarningAtPoint.v"
    sentence = "Check nat."
    source.write_text(f"{sentence}\n", encoding="utf-8")
    manager = RocqLspManager(
        RocqLspConfig(
            binary="coq-lsp",
            timeout_policy=TimeoutPolicy(
                request_seconds=30,
                cancel_grace_seconds=5,
                request_seconds_after_error=None,
            ),
            max_errors=1000000,
            workspace=tmp_path,
        )
    )
    try:
        result = await manager.run_at(
            str(source),
            "Inductive foo := A | B. "
            "Definition food (x : foo) := match x with | A => 0 | b => 1 end.",
            line=1,
            character=len(sentence) + 1,
        )
    finally:
        await manager.close()

    assert result.status == "complete"
    assert any(
        item.render().get("level") == "warning"
        and "Unused variable b" in item.text
        for item in result.output
    )


@pytest.mark.asyncio
async def test_run_at_can_run_require_before_check(tmp_path: Path) -> None:
    source = tmp_path / "RequireAtPoint.v"
    sentence = "Check nat."
    source.write_text(f"{sentence}\n", encoding="utf-8")
    manager = RocqLspManager(
        RocqLspConfig(
            binary="coq-lsp",
            timeout_policy=TimeoutPolicy(
                request_seconds=30,
                cancel_grace_seconds=5,
                request_seconds_after_error=None,
            ),
            max_errors=1000000,
            workspace=tmp_path,
        )
    )
    try:
        result = await manager.run_at(
            str(source),
            "From Stdlib Require Import Arith.PeanoNat. Check Nat.add_0_r.",
            line=1,
            character=len(sentence) + 1,
        )
    finally:
        await manager.close()

    assert result.status == "complete"
    assert any("Nat.add_0_r" in item.text for item in result.output)


@pytest.mark.asyncio
async def test_run_at_reports_command_failure(tmp_path: Path) -> None:
    source = tmp_path / "BadCommand.v"
    sentence = "Check nat."
    source.write_text(f"{sentence}\n", encoding="utf-8")
    manager = RocqLspManager(
        RocqLspConfig(
            binary="coq-lsp",
            timeout_policy=TimeoutPolicy(
                request_seconds=30,
                cancel_grace_seconds=5,
                request_seconds_after_error=None,
            ),
            max_errors=1000000,
            workspace=tmp_path,
        )
    )
    try:
        result = await manager.run_at(
            str(source),
            "Check does_not_exist.",
            line=1,
            character=len(sentence) + 1,
        )
    finally:
        await manager.close()

    assert result.status == "failed"
    assert result.message is not None
    assert "does_not_exist" in result.message


def _expected_tactic_failure() -> dict[str, object]:
    return {
        "level": "error",
        "message": "Tactic failure.",
        "location": "1:20 to 1:25",
        "sentence": "fail.",
        "proof_name": "Unnamed_thm",
        "proof_state": {"goals": [{"conclusion": "0 = 0"}]},
    }
