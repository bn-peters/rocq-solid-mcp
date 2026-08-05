Tactic Notation "sleep" integer(seconds) :=
  do seconds try solve [ timeout 1 (repeat eapply proj1) ].

(* Once the first error is reported, the whole check must stop after at most
   the after-error deadline. The second error is still reported within the
   window, and each slow sentence below finishes well within the normal
   per-sentence timeout, so only the overall after-error deadline can stop
   the chain: the cancellation lands inside the first slow sentence, far from
   any sentence boundary. *)
Lemma broken : True.
Proof.
  fail "FIRST".
Admitted.

Lemma also_broken : True.
Proof.
  fail "SECOND".
Admitted.

Lemma slow_chain : True.
Proof.
  sleep 10.
  sleep 10.
  easy.
Qed.
