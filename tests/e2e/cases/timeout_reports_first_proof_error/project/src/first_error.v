Tactic Notation "sleep" integer(seconds) :=
  do seconds try solve [ timeout 1 (repeat eapply proj1) ].

(* Two errors in one proof, then a slow tactic that outlives the request
   timeout. coq-lsp publishes the partial (timed-out) diagnostics for this
   proof out of source order, so the deduplicated check output must still
   report FIRST_ERROR, not the later SECOND_ERROR. *)
Lemma bug : True.
Proof.
  fail "FIRST_ERROR".
  fail "SECOND_ERROR".
  sleep 1.
  sleep 100.
  exact I.
Qed.
