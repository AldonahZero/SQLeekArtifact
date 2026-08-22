/**
 * @name Unsafe stale tuple descriptor cache access (improved)
 * @description Finds accesses to `tupdesc_id` or `tupDesc_identifier` fields that are not guarded by an equality check in functions that use tuple type cache, indicating potential stale cache usage when row types change.
 * @kind problem
 * @problem.severity error
 * @id cpp/stale-tupdesc-cache-v2
 */
import cpp

// Priority functions from 𝒻_T
predicate isPriorityFunction(Function f) {
  f.getName() in [
    "record_out", "ExecEvalRow", "ExecEvalRowNull", "text_to_cstring",
    "ExecJustScanVar", "ExecJustVarImpl", "ExecJustOuterVar", "ExecJustInnerVar",
    "ExecEvalRowNotNull", "wc_isprint_builtin", "ExecInitResultSlot",
    "ExecEvalWholeRowVar", "ExecJustVarVirtImpl", "ExecEvalArrayCoerce",
    "ExecEvalFieldSelect"
  ]
}

// Does the function call `lookup_type_cache`?
predicate callsLookupTypeCache(Function f) {
  exists(Call c |
    c.getTarget().getName() = "lookup_type_cache" and
    c.getEnclosingFunction() = f
  )
}

// Does the function have an if-statement that compares a field access of the given name with something?
predicate hasGuard(Function f, string fieldName) {
  exists(IfStmt ifs, BinaryOperation bin |
    bin = ifs.getCondition() and
    (bin.getOperator() = "==" or bin.getOperator() = "!=") and
    exists(FieldAccess guardAccess |
      (bin.getLeftOperand() = guardAccess or bin.getRightOperand() = guardAccess) and
      guardAccess.getTarget().getName() = fieldName
    )
  )
}

from FieldAccess access, Function func
where
  func = access.getEnclosingFunction() and
  // Only target fields named `tupdesc_id` or `tupDesc_identifier`
  (access.getTarget().getName() = "tupdesc_id" or access.getTarget().getName() = "tupDesc_identifier") and
  // Restrict to priority functions or functions that call lookup_type_cache
  (isPriorityFunction(func) or callsLookupTypeCache(func)) and
  // Exclude if the function already contains a guard on the same field name
  not hasGuard(func, access.getTarget().getName())
select access, "Unsafe access to stale tuple descriptor cache field '" + access.getTarget().getName() + "' in function " + func.getName()
