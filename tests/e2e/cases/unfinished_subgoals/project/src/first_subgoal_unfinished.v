Lemma foo : True -> True /\ True.
Proof.
  intros.
  split.
  - idtac. (* deliberately unfinished *)
  - easy.
Qed.
