Module Modes.
Inductive mode := Shared | Unique | Capsule.

Definition mode_rank (m : mode) : nat :=
  match m with
  | Shared => 0
  | Unique => 1
  | Capsule => 2
  end.

Definition can_promote (m : mode) : bool :=
  match m with
  | Shared => true
  | Unique => true
  | Capsule => false
  end.

Lemma capsule_not_promoted : can_promote Capsule = false.
Proof. reflexivity. Qed.

Lemma unique_rank : mode_rank Unique = 1.
Proof. reflexivity. Qed.
End Modes.
