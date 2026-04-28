from __future__ import annotations

from typing import Any


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
        return [normalize_proof_state(item) for item in value]
    if not isinstance(value, dict):
        return value

    if _looks_like_goal(value):
        return _normalize_goal(value)
    if _looks_like_goal_config(value):
        return _normalize_goal_config(value)
    if _looks_like_message(value):
        return _normalize_message(value)

    return {key: normalize_proof_state(item) for key, item in value.items()}


def _normalize_goal(value: dict[str, Any]) -> dict[str, Any]:
    info = value.get("info") if isinstance(value.get("info"), dict) else {}
    return {
        "name": normalize_proof_state(info.get("name")),
        "hyps": normalize_proof_state(value.get("hyps", [])),
        "ty": normalize_proof_state(value.get("ty")),
    }


def _normalize_goal_config(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "goals": normalize_proof_state(value.get("goals", [])),
        "stack": normalize_proof_state(value.get("stack", [])),
        "bullet": normalize_proof_state(value.get("bullet")),
        "shelf": normalize_proof_state(value.get("shelf", [])),
        "given_up": normalize_proof_state(value.get("given_up", [])),
    }


def _normalize_message(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    if "text" in result:
        result["text"] = normalize_proof_state(result["text"])
    return result


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
