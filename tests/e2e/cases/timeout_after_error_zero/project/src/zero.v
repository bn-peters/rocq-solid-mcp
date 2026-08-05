Tactic Notation "sleep" integer(seconds) :=
  do seconds try solve [ timeout 1 (repeat eapply proj1) ].

(* A fast error followed by a slow proof. A zero after-error deadline disables
   the feature, so the slow proof keeps running until the request deadline;
   the per-call override then re-enables the bounded exit. *)
Lemma ok : True.
Proof.
  exact I.
Qed.

Lemma broken : True.
Proof.
  fail "BROKEN".
Admitted.

Lemma slow : True.
Proof.
  sleep 30.
  easy.
Qed.
