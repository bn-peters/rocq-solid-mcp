"""End-to-end tests for the dune dependency-build integration.

These exercise real multi-theory dune Rocq projects (theories live in
subdirectories, the way ``generate_project_file`` layouts do) and observe two
bugs in the current integration:

* ``applied`` is derived from ``stderr.strip()`` being non-empty, but dune
  prints ``Entering directory``/``Leaving directory`` on stderr whenever it is
  invoked from a subdirectory of the workspace root. So an up-to-date
  dependency build looks like a real rebuild, the session is restarted on every
  check, and the persistent incremental cache is thrown away.
* ``run_at`` never builds dependencies, unlike ``check``, so a ``run_at`` whose
  ``Require`` needs an as-yet-uncompiled ``.vo`` fails.

The fixtures use the new Rocq build language (``(using rocq 0.11)`` with
``(rocq.theory ...)``) because the test opam switch ships ``rocq`` but not the
legacy ``coqc`` the deprecated ``(coq.theory ...)`` language shells out to.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from rocq_lsp_mcp.config import RocqLspConfig, TimeoutPolicy
from rocq_lsp_mcp.manager import RocqLspManager


def _dune_supports_rocq_lang() -> bool:
    dune = shutil.which("dune")
    if dune is None:
        return False
    try:
        version = subprocess.run(
            [dune, "--version"], check=True, text=True, capture_output=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return False
    parts = version.split(".")
    try:
        major, minor = int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return False
    return (major, minor) >= (3, 21)


pytestmark = pytest.mark.skipif(
    shutil.which("opam") is None
    or shutil.which("coq-lsp") is None
    or shutil.which("rocq") is None
    or not _dune_supports_rocq_lang(),
    reason="install opam, coq-lsp, rocq, and dune>=3.21 to run dune e2e tests",
)

SLEEP_TACTIC = (
    'Tactic Notation "sleep" integer(seconds) :=\n'
    "  do seconds try solve [ timeout 1 (repeat eapply proj1) ].\n"
)


def write_dune_project(root: Path, *, slow: bool) -> Path:
    """Create a two-theory dune project and return the client file path.

    ``theories`` holds a base theory; ``clients`` depends on it. Both theories
    live in subdirectories so dune emits the ``Entering directory`` stderr
    noise that trips the ``applied`` heuristic. When ``slow`` is set the client
    proof runs a five second ``sleep`` tactic so a spurious restart is
    observable through timing.
    """
    theories = root / "theories"
    clients = root / "clients"
    theories.mkdir(parents=True)
    clients.mkdir()
    (root / "dune-project").write_text(
        "(lang dune 3.21)\n(using rocq 0.11)\n"
        "(warnings (deprecated_coq_lang disabled))\n",
        encoding="utf-8",
    )
    (theories / "dune").write_text(
        "(rocq.theory\n (name basedep)\n (generate_project_file))\n",
        encoding="utf-8",
    )
    (clients / "dune").write_text(
        "(rocq.theory\n (name mainc)\n (generate_project_file)\n"
        " (theories basedep))\n",
        encoding="utf-8",
    )
    (theories / "Base.v").write_text(
        "Definition imported_value : nat := 41.\n", encoding="utf-8"
    )
    proof = "Proof.\n" + ("  sleep 5.\n" if slow else "") + "  reflexivity.\nQed.\n"
    client = clients / "UseBase.v"
    client.write_text(
        "From basedep Require Import Base.\n\n"
        + (SLEEP_TACTIC + "\n" if slow else "")
        + "Lemma imported_value_ok : imported_value = 41.\n"
        + proof,
        encoding="utf-8",
    )
    return client


def dune_build(root: Path, *targets: str) -> None:
    try:
        subprocess.run(
            ["dune", "build", *targets],
            cwd=root,
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:  # pragma: no cover - env guard
        output = "\n".join(part for part in (exc.stdout, exc.stderr) if part)
        pytest.skip(f"dune build unavailable in this switch:\n{output}")


def new_manager() -> RocqLspManager:
    return RocqLspManager(
        RocqLspConfig(
            binary="coq-lsp",
            timeout_policy=TimeoutPolicy(
                request_seconds=60,
                cancel_grace_seconds=5,
                command_seconds=None,
            ),
            max_errors=1000000,
            workspace=None,
        )
    )


@pytest.mark.asyncio
async def test_subdir_dune_recheck_does_not_restart_unchanged_file(
    tmp_path: Path,
) -> None:
    """Rechecking an unchanged file in a subdir dune project must not restart.

    Buggy behaviour: the up-to-date dependency build's ``Entering directory``
    stderr makes ``applied`` true, so ``second.restarted`` is ``True``.
    """
    project = tmp_path / "project"
    client = write_dune_project(project, slow=False)
    dune_build(project)

    manager = new_manager()
    try:
        first = await manager.check_file(str(client), long_running_seconds=0)
        second = await manager.check_file(str(client), long_running_seconds=0)
    finally:
        await manager.close()

    assert first.status == "complete"
    assert first.error_count == 0
    assert first.restarted is False
    assert second.status == "complete"
    assert second.error_count == 0
    assert second.restarted is False


@pytest.mark.asyncio
async def test_subdir_dune_cached_recheck_is_fast(tmp_path: Path) -> None:
    """A cached recheck stays fast; a spurious restart re-runs the slow proof.

    Buggy behaviour: the session is restarted every check, so the second check
    re-runs the five second ``sleep`` proof from scratch instead of returning
    the cached result.
    """
    project = tmp_path / "project"
    client = write_dune_project(project, slow=True)
    dune_build(project)

    manager = new_manager()
    try:
        start = time.monotonic()
        first = await manager.check_file(str(client), long_running_seconds=0)
        first_elapsed = time.monotonic() - start

        start = time.monotonic()
        second = await manager.check_file(str(client), long_running_seconds=0)
        second_elapsed = time.monotonic() - start
    finally:
        await manager.close()

    assert first.status == "complete"
    assert first.error_count == 0
    # Sanity: the first check actually ran the slow proof.
    assert first_elapsed >= 3
    assert second.status == "complete"
    assert second.error_count == 0
    assert second.restarted is False
    # A cached recheck must not re-run the five second proof.
    assert second_elapsed < 4


@pytest.mark.asyncio
async def test_run_at_builds_missing_dependencies(tmp_path: Path) -> None:
    """``run_at`` must build dependencies like ``check`` does.

    Only the generated ``_RocqProject`` files are built (so coq-lsp has load
    paths) while the dependency ``.vo`` is left uncompiled. Buggy behaviour:
    ``run_at`` never triggers a dune build, so the ``Require`` cannot find
    ``Base.vo`` and the reference is not found.
    """
    project = tmp_path / "project"
    client = write_dune_project(project, slow=False)
    dune_build(project, "clients/_RocqProject", "theories/_RocqProject")
    assert not list((project / "_build").rglob("*.vo")), "no .vo should exist yet"

    manager = new_manager()
    try:
        ran = await manager.run_at(
            str(client), "Check imported_value.", line=2, character=1
        )
        checked = await manager.check_file(str(client), long_running_seconds=0)
    finally:
        await manager.close()

    # check builds the dependency, proving the project itself is valid.
    assert checked.status == "complete"
    assert checked.error_count == 0
    # run_at should have built the dependency and resolved the reference.
    assert ran.status == "complete"
