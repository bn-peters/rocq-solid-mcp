from __future__ import annotations

import json
from typing import Any

from .models import Diagnostic, is_empty_value
from .pretty import normalize_proof_state


def build_diagnostics(
    raw: list[dict[str, Any]],
    document: dict[str, Any] | None,
    supplemental_proof_states: dict[str, Any] | None = None,
) -> list[Diagnostic]:
    proof_states = _proof_states_by_range(document)
    _merge_supplemental_proof_states(proof_states, supplemental_proof_states)
    diagnostics = [
        Diagnostic.from_lsp(
            diagnostic,
            proof_state=proof_states.get(
                _range_key(_diagnostic_sentence_range(diagnostic))
            ),
        )
        for diagnostic in raw
    ]
    return _agent_facing_filter(diagnostics)


def _agent_facing_filter(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    seen_error_proofs: set[str] = set()
    filtered: list[Diagnostic] = []
    for diagnostic in diagnostics:
        if diagnostic.severity == 1 and diagnostic.proof_name is not None:
            if diagnostic.proof_name in seen_error_proofs:
                continue
            seen_error_proofs.add(diagnostic.proof_name)
        filtered.append(diagnostic)
    return filtered


def _proof_states_by_range(document: dict[str, Any] | None) -> dict[str, Any]:
    if not document:
        return {}
    states: dict[str, Any] = {}
    for span in document.get("spans", []):
        if not isinstance(span, dict):
            continue
        goals = span.get("goals")
        range_ = span.get("range")
        if goals is not None and isinstance(range_, dict):
            normalized = normalize_proof_state(goals)
            if not is_empty_value(normalized):
                states[_range_key(range_)] = normalized
    return states


def _merge_supplemental_proof_states(
    states: dict[str, Any],
    supplemental: dict[str, Any] | None,
) -> None:
    if not supplemental:
        return
    for key, proof_state in supplemental.items():
        normalized = normalize_proof_state(proof_state)
        if not isinstance(normalized, dict) or is_empty_value(normalized):
            continue
        current = states.get(key)
        if isinstance(current, dict):
            for field, value in normalized.items():
                if field not in current and not is_empty_value(value):
                    current[field] = value
            continue
        states[key] = normalized


def _diagnostic_sentence_range(diagnostic: Any) -> dict[str, Any]:
    if not isinstance(diagnostic, dict):
        return {}
    data = diagnostic.get("data")
    if isinstance(data, dict) and isinstance(data.get("sentenceRange"), dict):
        return data["sentenceRange"]
    range_ = diagnostic.get("range")
    return range_ if isinstance(range_, dict) else {}


def _range_key(range_: dict[str, Any]) -> str:
    return json.dumps(range_, sort_keys=True, separators=(",", ":"))
