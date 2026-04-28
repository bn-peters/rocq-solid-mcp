From Stdlib Require Import Arith.

Definition client_value : nat := Nat.add 40 2.

Lemma client_value_ok : client_value = 42.
Proof. reflexivity. Qed.
