Tactic Notation "sleep" integer(seconds) :=
  do seconds try solve [ timeout 1 (repeat eapply proj1) ].

(* The normal per-sentence watchdog exempts the document's final sentence.
   After an error the after-error timeout applies even there, so the trailing
   slow sentence is cancelled instead of running for its full duration. *)
Lemma broken : True.
Proof.
  fail "BROKEN".
Admitted.

Goal True.
Proof.
sleep 30.
