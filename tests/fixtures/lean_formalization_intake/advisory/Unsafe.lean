axiom imported_truth : True

opaque hidden_value : Nat

theorem with_sorry : True := by
  sorry

theorem with_native_decide : True := by
  native_decide

@[implemented_by hidden_value]
def declared_value : Nat := 0

@[extern "lean_oracle"]
constant oracle_value : Nat
