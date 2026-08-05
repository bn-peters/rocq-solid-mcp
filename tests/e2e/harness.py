from __future__ import annotations

import difflib
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from rocq_lsp_mcp.config import RocqLspConfig, TimeoutPolicy
from rocq_lsp_mcp.manager import RocqLspManager
from rocq_lsp_mcp.models import RunAtResult, CheckResult

CASES_DIR = Path(__file__).parent / "cases"


def scenario_cases() -> list[Path]:
    return sorted(
        path for path in CASES_DIR.iterdir() if (path / "scenario.json").is_file()
    )


async def run_case(case_dir: Path, tmp_path: Path, *, promote: bool) -> None:
    scenario = _read_json(case_dir / "scenario.json")
    project_root = tmp_path / "project"
    shutil.copytree(
        case_dir / "project",
        project_root,
        ignore=shutil.ignore_patterns(
            "_build",
            "*.vo",
            "*.vos",
            "*.vok",
            "*.glob",
            "*.diags",
            "Makefile",
            "Makefile.conf",
            ".Makefile.d",
            ".*.aux",
        ),
    )
    _build_project(project_root, scenario)
    transcript: list[dict[str, Any]] = []

    manager = RocqLspManager(
        RocqLspConfig(
            binary="coq-lsp",
            timeout_policy=_timeout_policy(scenario),
            max_errors=1000000,
            workspace=None,
        )
    )
    try:
        transcript.extend(await _run_steps(manager, project_root, scenario["steps"]))
    finally:
        await manager.close()

    _check_or_promote(case_dir / "expected.json", transcript, promote=promote)


def _build_project(project_root: Path, scenario: dict[str, Any]) -> None:
    """Compile the whole project so coq-lsp can resolve imports.

    The backend is auto-detected from the copied project and can be overridden
    with the scenario's ``build`` key:

    - ``dune`` (default when a ``dune-project`` is present): generate every
      theory's ``_RocqProject`` via ``dune build <dir>/_RocqProject``. This
      writes the project files coq-lsp reads and compiles no ``.vo`` — so it
      never compiles a step-target file (many are intentionally broken or slow).
      The manager compiles each step's dependencies on demand during the run.
    - ``make`` (default when a ``_CoqProject`` but no ``dune-project`` is
      present): ``rocq makefile`` + ``make``. Only for all-valid sanity fixtures.
    - ``none`` (default for a legacy committed ``_RocqProject``): no build.
    """
    mode = scenario.get("build") or _detect_build_backend(project_root)
    if mode == "none":
        return
    if mode == "dune":
        _dune_build(project_root)
    elif mode == "make":
        _make_build(project_root)
    else:
        raise AssertionError(f"unknown e2e build mode: {mode}")


def _detect_build_backend(project_root: Path) -> str:
    if (project_root / "dune-project").is_file():
        return "dune"
    if (project_root / "_CoqProject").is_file():
        return "make"
    if (project_root / "_RocqProject").is_file():
        return "none"
    raise AssertionError(
        "e2e project has no dune-project, _CoqProject, or _RocqProject: "
        f"{project_root}"
    )


def _dune_build(project_root: Path) -> None:
    dune = shutil.which("dune")
    if dune is None or not _dune_supports_rocq_lang(dune):
        pytest.skip("install dune>=3.21 to run dune-based e2e scenarios")

    targets = [
        str(dune_file.parent.relative_to(project_root) / "_RocqProject")
        for dune_file in sorted(project_root.glob("**/dune"))
        if "_build" not in dune_file.parts
    ]
    if not targets:
        raise AssertionError(f"dune project has no theory dune files: {project_root}")

    result = subprocess.run(
        [dune, "build", *targets],
        cwd=project_root,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        raise AssertionError(f"dune build failed in {project_root}\n{output}")


def _make_build(project_root: Path) -> None:
    rocq = shutil.which("rocq")
    make = shutil.which("make")
    if rocq is None or make is None:
        pytest.skip("install rocq and make to run CoqMakefile e2e scenarios")

    generate = subprocess.run(
        [rocq, "makefile", "-f", "_CoqProject", "-o", "Makefile"],
        cwd=project_root,
        text=True,
        capture_output=True,
    )
    if generate.returncode != 0:
        output = "\n".join(part for part in (generate.stdout, generate.stderr) if part)
        raise AssertionError(f"rocq makefile failed in {project_root}\n{output}")

    build = subprocess.run(
        [make], cwd=project_root, text=True, capture_output=True
    )
    if build.returncode != 0:
        output = "\n".join(part for part in (build.stdout, build.stderr) if part)
        raise AssertionError(f"make failed in {project_root}\n{output}")


def _dune_supports_rocq_lang(dune: str) -> bool:
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


def _timeout_policy(scenario: dict[str, Any]) -> TimeoutPolicy:
    policy = scenario.get("timeout_policy", {})
    return TimeoutPolicy(
        request_seconds=policy.get("request_seconds", 30),
        cancel_grace_seconds=policy.get("cancel_grace_seconds", 5),
        command_seconds=policy.get("command_seconds"),
        request_seconds_after_error=policy.get("request_seconds_after_error"),
    )


async def _run_steps(
    manager: RocqLspManager,
    project_root: Path,
    steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    transcript: list[dict[str, Any]] = []
    for step in steps:
        action = step["action"]
        file = step["file"]
        path = project_root / file

        if action == "create":
            if path.exists():
                raise AssertionError(f"create target already exists: {file}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(step["text"], encoding="utf-8")
            transcript.append(_step_record(step))
            continue

        if action == "append":
            path.write_text(
                path.read_text(encoding="utf-8") + step["text"],
                encoding="utf-8",
            )
            transcript.append(_step_record(step))
            continue

        if action == "replace":
            old = step["old"]
            content = path.read_text(encoding="utf-8")
            count = content.count(old)
            if count != 1:
                raise AssertionError(
                    f"{file}: expected one occurrence of {old!r}, found {count}"
                )
            path.write_text(content.replace(old, step["new"], 1), encoding="utf-8")
            transcript.append(_step_record(step))
            continue

        if action == "check":
            start = time.monotonic()
            result = await manager.check_file(
                str(path),
                command_timeout_seconds=step.get("command_timeout_seconds"),
                timeout_after_error_seconds=step.get(
                    "timeout_after_error_seconds"
                ),
                overall_timeout_seconds=step.get("overall_timeout_seconds"),
                long_running_seconds=step.get("long_running_seconds", 3),
            )
            elapsed = time.monotonic() - start
            _check_timing(step, elapsed)
            transcript.append(
                {
                    **_step_record(step),
                    "result": _result_record(result, project_root, step),
                }
            )
            continue

        if action == "run_at":
            start = time.monotonic()
            result = await manager.run_at(
                str(path),
                step["commands"],
                step["line"],
                step.get("character", 1),
                overall_timeout_seconds=step.get("overall_timeout_seconds"),
            )
            elapsed = time.monotonic() - start
            _check_timing(step, elapsed)
            transcript.append(
                {
                    **_step_record(step),
                    "commands": step["commands"],
                    "result": _result_record(result, project_root, step),
                }
            )
            continue

        raise AssertionError(f"unknown e2e action: {action}")
    return transcript


def _check_timing(step: dict[str, Any], elapsed: float) -> None:
    if "min_seconds" in step and elapsed < step["min_seconds"]:
        raise AssertionError(
            f"{step['label']}: expected {step['action']} to take at least "
            f"{step['min_seconds']:.3f}s, took {elapsed:.3f}s"
        )
    if "max_seconds" in step and elapsed >= step["max_seconds"]:
        raise AssertionError(
            f"{step['label']}: expected {step['action']} to take less than "
            f"{step['max_seconds']:.3f}s, took {elapsed:.3f}s"
        )


def _step_record(step: dict[str, Any]) -> dict[str, Any]:
    record = {
        "step": step["label"],
        "action": step["action"],
    }
    if "file" in step:
        record["file"] = step["file"]
    if "command_timeout_seconds" in step:
        record["command_timeout_seconds"] = step["command_timeout_seconds"]
    if "timeout_after_error_seconds" in step:
        record["timeout_after_error_seconds"] = step["timeout_after_error_seconds"]
    return record


def _result_record(
    result: CheckResult | RunAtResult, project_root: Path, step: dict[str, Any]
) -> dict[str, Any]:
    if isinstance(result, CheckResult):
        return _scrub_value(result.render(), project_root)
    return _scrub_value(result.render(), project_root)


def _scrub_value(value: Any, project_root: Path) -> Any:
    if isinstance(value, str):
        return value.replace(project_root.as_uri(), "file://$PROJECT").replace(
            str(project_root),
            "$PROJECT",
        )
    if isinstance(value, list):
        return [_scrub_value(item, project_root) for item in value]
    if isinstance(value, dict):
        return {
            key: _scrub_value(item, project_root)
            for key, item in value.items()
        }
    return value


def _check_or_promote(
    expected_path: Path,
    actual: list[dict[str, Any]],
    *,
    promote: bool,
) -> None:
    actual_text = _json_text(actual)
    if promote:
        expected_path.write_text(actual_text, encoding="utf-8")
        return

    if not expected_path.exists():
        pytest.fail(f"missing {expected_path}; run `make promote` to create it")

    expected_text = expected_path.read_text(encoding="utf-8")
    if expected_text == actual_text:
        return

    diff = "\n".join(
        difflib.unified_diff(
            expected_text.splitlines(),
            actual_text.splitlines(),
            fromfile=str(expected_path),
            tofile="actual",
            lineterm="",
        )
    )
    pytest.fail(f"e2e transcript changed; run `make promote` if intended\n{diff}")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2) + "\n"
