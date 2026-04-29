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
from rocq_lsp_mcp.models import CompileResult

CASES_DIR = Path(__file__).parent / "cases"


def scenario_cases() -> list[Path]:
    return sorted(
        path for path in CASES_DIR.iterdir() if (path / "scenario.json").is_file()
    )


async def run_case(case_dir: Path, tmp_path: Path, *, promote: bool) -> None:
    scenario = _read_json(case_dir / "scenario.json")
    project_root = tmp_path / "project"
    shutil.copytree(case_dir / "project", project_root)
    transcript = _run_setup(project_root, scenario.get("setup", []))

    manager = RocqLspManager(
        RocqLspConfig(
            binary="coq-lsp",
            timeout_policy=TimeoutPolicy(
                request_seconds=30,
                cancel_grace_seconds=5,
            ),
            max_errors=1000000,
            workspace=None,
        )
    )
    try:
        transcript.extend(await _run_steps(manager, project_root, scenario["steps"]))
    finally:
        await manager.close()

    _check_or_promote(case_dir / "expected.json", transcript, promote=promote)


def _run_setup(project_root: Path, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    transcript: list[dict[str, Any]] = []
    for step in steps:
        action = step["action"]
        if action == "rocq c":
            _run_rocq_c(project_root, step)
            transcript.append(_step_record(step))
            continue

        raise AssertionError(f"unknown e2e setup action: {action}")
    return transcript


def _run_rocq_c(project_root: Path, step: dict[str, Any]) -> None:
    rocq = shutil.which("rocq")
    if rocq is None:
        pytest.skip("install rocq to run e2e scenarios with compiled dependencies")

    file = step["file"]
    try:
        subprocess.run(
            [rocq, "c", *step.get("args", []), file],
            cwd=project_root,
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        output = "\n".join(part for part in (exc.stdout, exc.stderr) if part)
        raise AssertionError(f"rocq c failed for {file}\n{output}") from exc


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

        if action == "compile":
            start = time.monotonic()
            result = await manager.compile_file(str(path))
            elapsed = time.monotonic() - start
            _check_timing(step, elapsed)
            transcript.append(
                {
                    **_step_record(step),
                    "result": _result_record(result, project_root),
                }
            )
            continue

        raise AssertionError(f"unknown e2e action: {action}")
    return transcript


def _check_timing(step: dict[str, Any], elapsed: float) -> None:
    if "min_seconds" in step and elapsed < step["min_seconds"]:
        raise AssertionError(
            f"{step['label']}: expected compile to take at least "
            f"{step['min_seconds']:.3f}s, took {elapsed:.3f}s"
        )
    if "max_seconds" in step and elapsed >= step["max_seconds"]:
        raise AssertionError(
            f"{step['label']}: expected compile to take less than "
            f"{step['max_seconds']:.3f}s, took {elapsed:.3f}s"
        )


def _step_record(step: dict[str, Any]) -> dict[str, Any]:
    record = {
        "step": step["label"],
        "action": step["action"],
    }
    if "file" in step:
        record["file"] = step["file"]
    return record


def _result_record(result: CompileResult, project_root: Path) -> dict[str, Any]:
    return _scrub_value(result.model_dump(), project_root)


def _scrub_value(value: Any, project_root: Path) -> Any:
    if isinstance(value, str):
        return value.replace(project_root.as_uri(), "file://$PROJECT").replace(
            str(project_root),
            "$PROJECT",
        )
    if isinstance(value, list):
        return [_scrub_value(item, project_root) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_value(item, project_root) for key, item in value.items()}
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
