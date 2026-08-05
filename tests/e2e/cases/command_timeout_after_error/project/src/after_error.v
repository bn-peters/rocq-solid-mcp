Tactic Notation "sleep" integer(seconds) :=
  do seconds try solve [ timeout 1 (repeat eapply proj1) ].

(* Before any error the normal per-sentence timeout applies, so the first
   slow proof survives. After the first error the shorter after-error timeout
   takes over: later fast errors are still reported, and the slow proof at
   the end is cancelled quickly. *)
Lemma slow_ok : True.
Proof.
  sleep 4.
  easy.
Qed.

Lemma broken : True.
Proof.
  fail "FIRST".
Admitted.

Lemma also_broken : True.
Proof.
  fail "SECOND".
Admitted.

Lemma slow_after : True.
Proof.
  sleep 30.
  easy.
Qed.
