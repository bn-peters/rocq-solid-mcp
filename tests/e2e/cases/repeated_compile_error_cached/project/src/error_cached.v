Tactic Notation "sleep" integer(seconds) :=
  do seconds try solve [ timeout 1 (repeat eapply proj1) ].

Goal True.
Proof.
  sleep 3.
  easy.
Qed.

Definition bad : False := I.
