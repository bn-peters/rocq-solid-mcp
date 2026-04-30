Notation "'foo' x" := x (at level 0).
Notation "'foo' x" := x (at level 0).

Lemma bad_missing_tactic : True.
Proof.
  missing_tactic.
Qed.

Lemma bad_invalid_tactic : True.
Proof.
  invalid_tactic.
Qed.
