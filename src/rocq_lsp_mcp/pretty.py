from __future__ import annotations

from typing import Any

from .models import is_empty_value, pruned_dict


def pp_to_text(value: Any) -> str:
    """Render rocq-lsp's serialized Pp tree into readable plain text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if not isinstance(value, list) or not value:
        return str(value)

    tag = value[0]
    if tag == "Pp_empty":
        return ""
    if tag == "Pp_string":
        return str(value[1]) if len(value) > 1 else ""
    if tag == "Pp_sized_string":
        return str(value[2]) if len(value) > 2 else ""
    if tag == "Pp_glue":
        return "".join(pp_to_text(item) for item in _list_at(value, 1))
    if tag in {"Pp_box", "Pp_tag"}:
        return pp_to_text(value[2]) if len(value) > 2 else ""
    if tag == "Pp_print_break":
        indent = value[2] if len(value) > 2 and isinstance(value[2], int) else 0
        return "\n" + (" " * max(indent, 0))
    if tag == "Pp_force_newline":
        return "\n"
    if tag == "Pp_comment":
        return "\n".join(str(item) for item in _list_at(value, 1))
    return str(value)


def normalize_proof_state(value: Any) -> Any:
    """Turn rocq-lsp goal answers into agent-facing JSON."""
    if _is_pp(value):
        return pp_to_text(value)
    if isinstance(value, list):
        return [
            normalized
            for item in value
            if not is_empty_value(normalized := normalize_proof_state(item))
        ]
    if not isinstance(value, dict):
        return value

    if _looks_like_goal(value):
        return _normalize_goal(value)
    if _looks_like_goal_config(value):
        return _normalize_goal_config(value)
    if _looks_like_message(value):
        return _normalize_message(value)

    result: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"textDocument", "position", "range", "error", "program"}:
            continue
        normalized = normalize_proof_state(item)
        if key == "goals" and isinstance(normalized, dict):
            result.update(normalized)
            continue
        if not is_empty_value(normalized):
            result[key] = normalized
    return result


def _normalize_goal(value: dict[str, Any]) -> dict[str, Any]:
    info = value.get("info") if isinstance(value.get("info"), dict) else {}
    return pruned_dict(
        {
            "name": normalize_proof_state(info.get("name")),
            "hypotheses": _normalize_hypotheses(value.get("hyps", [])),
            "conclusion": normalize_proof_state(value.get("ty")),
        }
    )


def _normalize_goal_config(value: dict[str, Any]) -> dict[str, Any]:
    goals = normalize_proof_state(value.get("goals", []))
    given_up = normalize_proof_state(value.get("given_up", []))
    result = {
        "goals": goals,
        "bullet": normalize_proof_state(value.get("bullet")),
        "shelved_goals": normalize_proof_state(value.get("shelf", [])),
    }
    if given_up != goals:
        result["given_up_goals"] = given_up
    return pruned_dict(result)


def _normalize_message(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    if "text" in result:
        result["text"] = normalize_proof_state(result["text"])
    return pruned_dict(result)


def _normalize_hypotheses(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    hypotheses: list[str] = []
    for item in value:
        hypothesis = _normalize_hypothesis(item)
        if hypothesis:
            hypotheses.append(hypothesis)
    return hypotheses


def _normalize_hypothesis(value: Any) -> str | None:
    if not isinstance(value, dict):
        normalized = normalize_proof_state(value)
        return str(normalized) if normalized is not None else None

    names = _hypothesis_names(value.get("names", []))
    ty = normalize_proof_state(value.get("ty"))
    definition = normalize_proof_state(value.get("def"))
    if not names:
        return str(ty) if ty is not None else None

    prefix = " ".join(names)
    if definition is not None and ty is not None:
        return f"{prefix} := {definition} : {ty}"
    if ty is not None:
        return f"{prefix} : {ty}"
    if definition is not None:
        return f"{prefix} := {definition}"
    return prefix


def _hypothesis_names(value: Any) -> list[str]:
    normalized = normalize_proof_state(value)
    if isinstance(normalized, list):
        return [str(item) for item in normalized if item is not None]
    if normalized is None:
        return []
    return [str(normalized)]


def _is_pp(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) > 0
        and isinstance(value[0], str)
        and value[0].startswith("Pp_")
    )


def _looks_like_goal(value: dict[str, Any]) -> bool:
    return "hyps" in value and "ty" in value


def _looks_like_goal_config(value: dict[str, Any]) -> bool:
    return "goals" in value and "stack" in value and "shelf" in value


def _looks_like_message(value: dict[str, Any]) -> bool:
    return "text" in value and "level" in value


def _list_at(value: list[Any], index: int) -> list[Any]:
    item = value[index] if len(value) > index else []
    return item if isinstance(item, list) else []
