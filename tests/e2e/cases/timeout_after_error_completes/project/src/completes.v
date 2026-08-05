(* Multiple errors in a file whose remaining sentences are all fast. The
   after-error timeout must not cancel anything: the check completes and
   reports every error. *)
Lemma first_broken : True.
Proof.
  fail "FIRST".
Admitted.

Lemma middle_ok : True.
Proof.
  exact I.
Qed.

Lemma second_broken : True.
Proof.
  fail "SECOND".
Admitted.

Lemma last_ok : True.
Proof.
  exact I.
Qed.
