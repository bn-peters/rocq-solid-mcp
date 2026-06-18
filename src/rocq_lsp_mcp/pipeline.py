from __future__ import annotations

import json
from typing import Any

from .models import Diagnostic, is_empty_value, source_text_for_range
from .pretty import normalize_proof_state


def build_diagnostics(
    raw: list[dict[str, Any]],
    document: dict[str, Any] | None,
    *,
    content: str | None = None,
    supplemental_proof_states: dict[str, Any] | None = None,
) -> list[Diagnostic]:
    proof_states = _proof_states_by_range(document)
    _merge_supplemental_proof_states(proof_states, supplemental_proof_states)
    diagnostics: list[Diagnostic] = []
    for diagnostic in raw:
        sentence_range = _diagnostic_sentence_range(diagnostic)
        diagnostics.append(
            Diagnostic.from_lsp(
                diagnostic,
                proof_state=proof_states.get(_range_key(sentence_range)),
                sentence=(
                    source_text_for_range(content, sentence_range)
                    if content is not None
                    else None
                ),
            )
        )
    return diagnostics


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
