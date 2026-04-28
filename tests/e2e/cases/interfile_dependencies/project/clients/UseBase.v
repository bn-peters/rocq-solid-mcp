From dependency_demo Require Import Base.

Definition client_value : nat := S imported_value.

Lemma client_value_ok : client_value = 42.
Proof. unfold client_value, imported_value. reflexivity. Qed.
