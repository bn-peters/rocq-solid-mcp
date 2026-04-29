Lemma foo : True -> True /\ True.
Proof.
  intros.
  split.
  - easy.
  - idtac. (* deliberately unfinished *)
Qed.
