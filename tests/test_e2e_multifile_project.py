from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from rocq_lsp_mcp.config import RocqLspConfig
from rocq_lsp_mcp.manager import RocqLspManager

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_ROCQ_LSP_E2E") or shutil.which("coq-lsp") is None,
    reason="set RUN_ROCQ_LSP_E2E=1 and install coq-lsp to run",
)


def write_multifile_project(root: Path) -> tuple[Path, Path]:
    (root / "src/lang").mkdir(parents=True)
    (root / "_CoqProject").write_text("-Q src multifile_demo\n", encoding="utf-8")

    base = root / "src/base.v"
    base.write_text(
        """
Module ModeBase.
Definition mode_id (n : nat) : nat := n.
Lemma mode_id_spec n : mode_id n = n.
Proof. reflexivity. Qed.
End ModeBase.
""".lstrip(),
        encoding="utf-8",
    )

    modes = root / "src/lang/modes.v"
    modes.write_text(
        """
Module Modes.
Inductive mode := Shared | Unique | Capsule.

Definition mode_rank (m : mode) : nat :=
  match m with
  | Shared => 0
  | Unique => 1
  | Capsule => 2
  end.

Definition can_promote (m : mode) : bool :=
  match m with
  | Shared => true
  | Unique => true
  | Capsule => false
  end.

Lemma capsule_not_promoted : can_promote Capsule = false.
Proof. reflexivity. Qed.

Lemma unique_rank : mode_rank Unique = 1.
Proof. reflexivity. Qed.
End Modes.
""".lstrip(),
        encoding="utf-8",
    )
    return base, modes


@pytest.mark.asyncio
async def test_multifile_project_late_edit_reports_new_errors(tmp_path: Path) -> None:
    base, modes = write_multifile_project(tmp_path)
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
        base_result = await manager.compile_file(str(base))
        clean_modes_result = await manager.compile_file(str(modes))

        assert base_result.status == "complete"
        assert base_result.error_count == 0
        assert clean_modes_result.status == "complete"
        assert clean_modes_result.error_count == 0

        modes.write_text(
            modes.read_text(encoding="utf-8")
            + "\nCheck missing_late_multifile_name_1.\n"
            + "Check missing_late_multifile_name_2.\n",
            encoding="utf-8",
        )

        broken_modes_result = await manager.compile_file(str(modes))
    finally:
        await manager.close()

    messages = "\n".join(error.message for error in broken_modes_result.errors)
    assert broken_modes_result.status == "complete"
    assert broken_modes_result.version == 2
    assert broken_modes_result.error_count >= 2
    assert "missing_late_multifile_name_1" in messages
    assert "missing_late_multifile_name_2" in messages
