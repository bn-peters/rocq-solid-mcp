Tactic Notation "sleep" integer(seconds) :=
  do seconds try solve [ timeout 1 (repeat eapply proj1) ].

(* Once the first error is reported, the whole check must stop after at most
   the after-error deadline. Every sentence in the chain below finishes well
   within the normal per-sentence timeout, so only the overall after-error
   deadline can stop the chain. *)
Lemma broken : True.
Proof.
  fail "FIRST".
Admitted.

Lemma slow_chain : True.
Proof.
  sleep 3.
  sleep 3.
  sleep 3.
  sleep 3.
  sleep 3.
  easy.
Qed.
