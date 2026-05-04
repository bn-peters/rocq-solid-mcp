Inductive foo := A | B.
Definition food (x : foo) := match x with | A => 0 | b => 1 end.

Lemma bad_missing_tactic : True.
Proof.
  missing_tactic.
Qed.

Lemma bad_invalid_tactic : True.
Proof.
  invalid_tactic.
Qed.
