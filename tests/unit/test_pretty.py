from __future__ import annotations

from rocq_solid_mcp.pretty import normalize_proof_state, pp_to_text


def test_pp_to_text_handles_common_rocq_lsp_nodes() -> None:
    assert pp_to_text(["Pp_empty"]) == ""
    assert pp_to_text(["Pp_string", "hello"]) == "hello"
    assert pp_to_text(["Pp_sized_string", 5, "hello"]) == "hello"
    assert pp_to_text(["Pp_glue", [["Pp_string", "a"], ["Pp_string", "b"]]]) == "ab"
    assert pp_to_text(["Pp_box", ["Pp_hovbox", 0], ["Pp_string", "boxed"]]) == "boxed"
    assert pp_to_text(["Pp_tag", "tag", ["Pp_string", "tagged"]]) == "tagged"
    assert pp_to_text(["Pp_print_break", 1, 2]) == "\n  "
    assert pp_to_text(["Pp_force_newline"]) == "\n"


def test_normalize_proof_state_simplifies_goals_and_hypotheses() -> None:
    proof_state = {
        "goals": {
            "goals": [
                {
                    "info": {"evar": ["Ser_Evar", 2], "name": None},
                    "hyps": [
                        {"names": ["H"], "def": None, "ty": ["Pp_string", "True"]},
                        {
                            "names": ["n"],
                            "def": ["Pp_string", "0"],
                            "ty": ["Pp_string", "nat"],
                        },
                    ],
                    "ty": ["Pp_string", "0 = 0"],
                }
            ],
            "stack": [],
            "bullet": None,
            "shelf": [],
            "given_up": [],
        },
        "error": [
            "Pp_glue",
            [
                ["Pp_string", "first"],
                ["Pp_print_break", 1, 0],
                ["Pp_string", "second"],
            ],
        ],
    }

    assert normalize_proof_state(proof_state) == {
        "goals": [
            {
                "hypotheses": ["H : True", "n := 0 : nat"],
                "conclusion": "0 = 0",
            }
        ]
    }
