Definition iProp (Σ : unit) : Type := unit.
Definition seal {A} (x : A) : Type := unit.

Class Foo := unitt : Type.
Global Instance token_world_le_preorder : Foo.
Proof.
  (* omitted deliberately *)
Qed.
(* Admitted. *)

Section defs.
  Context `{Σ : unit}.

  Definition token_inv_def (P : unit -> iProp Σ) : Prop := True.
  Definition token_inv_aux : seal (@token_inv_def). Proof. easy. Qed.
  Check token_inv_aux.

End defs.
