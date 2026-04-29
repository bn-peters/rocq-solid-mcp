Definition iProp (Σ : unit) : Type := unit.
Definition seal {A} (x : A) : Type := unit.

Record Foo := MkFoo { a : unit ; b : unit }.

Program Definition foo : Foo := {| a := _ |}.
Next Obligation.
  easy.
Qed.
Next Obligation.
  (* easy. *)
Qed.

Section defs.
  Context `{Σ : unit}.

  Definition token_inv_def (P : unit -> iProp Σ) : Prop := True.
  Definition token_inv_aux : seal (@token_inv_def). Proof. easy. Qed.
  Check token_inv_aux.

End defs.
