Tactic Notation "sleep" integer(seconds) :=
  do seconds try solve [ timeout 1 (repeat eapply proj1) ].

Goal True.
Proof.
  sleep 2.
  easy.
Qed.

Goal True.
Proof.
  sleep 7.
  easy.
Qed.

Goal True.
Proof.
  fail.
Qed.
