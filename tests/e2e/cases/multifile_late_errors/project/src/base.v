Module ModeBase.
Definition mode_id (n : nat) : nat := n.
Lemma mode_id_spec n : mode_id n = n.
Proof. reflexivity. Qed.
End ModeBase.
