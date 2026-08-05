from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from rocq_lsp_mcp.config import RocqLspConfig, TimeoutPolicy
from rocq_lsp_mcp.manager import RocqLspManager

pytestmark = pytest.mark.skipif(
    shutil.which("opam") is None
    or shutil.which("coq-lsp") is None
    or shutil.which("rocq") is None,
    reason="install opam, coq-lsp, and rocq to run clear_cache e2e tests",
)


@pytest.mark.asyncio
async def test_check_clear_cache_refreshes_recompiled_dependencies(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    theories = project / "theories"
    clients = project / "clients"
    theories.mkdir(parents=True)
    clients.mkdir()
    (project / "_RocqProject").write_text(
        "-Q theories restart_demo\n-Q clients restart_demo_clients\n",
        encoding="utf-8",
    )

    base = theories / "Base.v"
    client = clients / "UseBase.v"
    base.write_text(base_source(41), encoding="utf-8")
    client.write_text(client_source(41), encoding="utf-8")
    compile_dependency(project)

    manager = RocqLspManager(
        RocqLspConfig(
            binary="coq-lsp",
            timeout_policy=TimeoutPolicy(
                request_seconds=30,
                cancel_grace_seconds=5,
                command_seconds=5,
                request_seconds_after_error=None,
            ),
            max_errors=1000000,
            workspace=None,
        )
    )
    try:
        first = await manager.check_file(str(client), long_running_seconds=0)

        base.write_text(base_source(40), encoding="utf-8")
        client.write_text(client_source(40), encoding="utf-8")
        compile_dependency(project)

        stale = await manager.check_file(str(client), long_running_seconds=0)
        refreshed = await manager.check_file(
            str(client),
            long_running_seconds=0,
            clear_cache=True,
        )
    finally:
        await manager.close()

    assert first.status == "complete"
    assert first.error_count == 0
    assert stale.status == "complete"
    assert stale.error_count > 0
    assert any(
        diagnostic.message == 'Unable to unify "40" with "imported_value".'
        for diagnostic in stale.errors
    )
    assert refreshed.status == "complete"
    assert refreshed.error_count == 0
    assert refreshed.restarted is True


@pytest.mark.asyncio
async def test_check_restarts_when_coqproject_changes(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    theories = project / "theories"
    clients = project / "clients"
    theories.mkdir(parents=True)
    clients.mkdir()
    project_file = project / "_CoqProject"
    project_file.write_text(
        "-Q theories restart_before\n-Q clients restart_clients\n",
        encoding="utf-8",
    )

    base = theories / "Base.v"
    client = clients / "UseBase.v"
    base.write_text(base_source(41), encoding="utf-8")
    client.write_text(client_source(41, library="restart_before"), encoding="utf-8")
    compile_dependency(project, library="restart_before")

    manager = RocqLspManager(
        RocqLspConfig(
            binary="coq-lsp",
            timeout_policy=TimeoutPolicy(
                request_seconds=30,
                cancel_grace_seconds=5,
                command_seconds=5,
                request_seconds_after_error=None,
            ),
            max_errors=1000000,
            workspace=None,
        )
    )
    try:
        first = await manager.check_file(str(client), long_running_seconds=0)

        project_file.write_text(
            "-Q theories restart_after\n-Q clients restart_clients\n",
            encoding="utf-8",
        )
        client.write_text(
            client_source(41, library="restart_after"),
            encoding="utf-8",
        )
        compile_dependency(project, library="restart_after")
        stale = await manager.check_file(str(client), long_running_seconds=0)
        restarted = await manager.check_file(
            str(client),
            long_running_seconds=0,
            clear_cache=True,
        )
    finally:
        await manager.close()

    assert first.status == "complete"
    assert first.error_count == 0
    assert first.restarted is False
    assert stale.status == "complete"
    assert stale.error_count > 0
    assert stale.restarted is False
    assert restarted.status == "complete"
    assert restarted.error_count == 0
    assert restarted.restarted is True


def compile_dependency(project: Path, *, library: str = "restart_demo") -> None:
    try:
        subprocess.run(
            ["rocq", "c", "-Q", "theories", library, "theories/Base.v"],
            cwd=project,
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        output = "\n".join(part for part in (exc.stdout, exc.stderr) if part)
        raise AssertionError(f"rocq c failed for theories/Base.v\n{output}") from exc


def base_source(value: int) -> str:
    return f"Definition imported_value : nat := {value}.\n"


def client_source(value: int, *, library: str = "restart_demo") -> str:
    return (
        f"From {library} Require Import Base.\n\n"
        f"Lemma imported_value_current : imported_value = {value}.\n"
        "Proof. reflexivity. Qed.\n"
    )
