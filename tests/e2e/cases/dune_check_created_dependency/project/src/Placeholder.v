(* Keeps the theory non-empty at setup time so dune generates a
   representative _RocqProject. An empty theory is emitted with -noinit,
   which is not how a real project's project file looks. This file is never
   required by a.v or b.v and is not compiled by the setup build. *)
