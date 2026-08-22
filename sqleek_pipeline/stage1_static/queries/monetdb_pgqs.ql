/**
 * @name MonetDB PGQS SQL/MAL/GDK risk anchors
 * @description Hand-written Stage-1 Query A for MonetDB. It reports Stage-0
 *              priority functions that touch SQL relational descriptors, MAL
 *              runtime state, BAT/GDK column storage, stream buffers, or
 *              embedded-client query state. No external LLM-generated QL is used.
 * @kind problem
 * @problem.severity warning
 * @id monetdb/pgqs-sql-mal-gdk-risk
 */

import cpp
import priority_functions

predicate isMonetDbSource(Function f) {
  f.getFile().getRelativePath().regexpMatch(
    "(^|.*/)(sql/(server|common|backends/monet5|storage)/.*|" +
    "monetdb5/(mal|optimizer|modules)/.*|gdk/.*|common/(stream|utils)/.*|" +
    "clients/(odbc|mapilib)/.*|tools/monetdbe/.*)"
  )
}

predicate isMonetDbDriver(Function f) {
  f.getName().regexpMatch(
    "^(SQLparser|SQLparser_body|SQLengine|SQLengine_|SQLprepare|" +
    "SQLexecutePrepared|monetdbe_query|monetdbe_query_internal|" +
    "monetdbe_query_remote|runMALsequence|MALrun)$"
  )
}

predicate isMonetDbSemanticHotFunction(Function f) {
  f.getName().regexpMatch(
    "^(rel_.*|exp_.*|stmt_.*|mvc_.*|sql_.*|bind_.*|push_up_.*|" +
    "run_exp_rewriter|OPT.*|mal_.*|.*Mal.*|BAT.*|GDK.*|BBP.*|" +
    "bs_.*|stream_.*|write_out|read.*|monetdbe_.*)$"
  )
}

predicate isMonetDbPriorityContext(Function f) {
  isMonetDbSource(f) and
  (isPriorityFunction(f) or isMonetDbDriver(f) or isMonetDbSemanticHotFunction(f))
}

bindingset[name]
predicate hasMonetDbStateName(string name) {
  name.regexpMatch(
    ".*(mvc|rel|exp|stmt|sql|query|client|cntxt|backend|be|sa|allocator|" +
    "mal|mb|stk|pci|blk|bat|BAT|bbp|heap|tail|head|column|type|schema|" +
    "stream|buf|buffer|blob|result|cursor|mapi|odbc|descriptor|param).*"
  )
}

predicate hasMonetDbStateType(Variable v) {
  v.getType().toString().regexpMatch(
    ".*(mvc|sql_rel|sql_exp|sql_subtype|sql_table|sql_column|Client|" +
    "backend|MalBlk|MalStk|InstrPtr|BAT|BATiter|Heap|stream|" +
    "monetdbe_.*|ODBC|SQLHANDLE).*"
  )
}

predicate touchesMonetDbStateIn(Function f) {
  exists(FieldAccess fa |
    fa.getEnclosingFunction() = f and
    hasMonetDbStateName(fa.getTarget().getName())
  )
  or
  exists(VariableAccess va, Variable v |
    va.getEnclosingFunction() = f and
    va.getTarget() = v and
    (hasMonetDbStateName(v.getName()) or hasMonetDbStateType(v))
  )
}

predicate hasMonetDbSemanticCallIn(Function f) {
  exists(FunctionCall call, Function target |
    call.getEnclosingFunction() = f and
    call.getTarget() = target and
    target.getName().regexpMatch(
      "^(SQLparser|SQLparser_body|SQLengine|SQLengine_|rel_.*|exp_.*|" +
      "mvc_.*|sql_.*|bind_.*|push_up_.*|run_exp_rewriter|" +
      "runMALsequence|MALrun|OPT.*|BAT.*|GDK.*|BBP.*|" +
      "bs_.*|stream_.*|write_out|monetdbe_.*)$"
    )
  )
}

predicate hasMemorySensitiveCallIn(Function f) {
  exists(FunctionCall call, Function target |
    call.getEnclosingFunction() = f and
    call.getTarget() = target and
    target.getName().regexpMatch(
      "^(memcpy|memmove|memset|malloc|calloc|realloc|GDKmalloc|GDKzalloc|" +
      "GDKrealloc|GDKstrdup|GDKstrndup|GDKfree|HEAPalloc|HEAPextend|" +
      "createException|throw|sa_alloc|sa_zalloc|sa_realloc|sa_strdup)$"
    )
  )
}

from Function f, string message
where
  isMonetDbPriorityContext(f) and
  (
    touchesMonetDbStateIn(f) and
    message = "MonetDB priority function '" + f.getName() +
      "' touches SQL/MAL/BAT/stream descriptor state"
    or
    hasMonetDbSemanticCallIn(f) and
    message = "MonetDB priority function '" + f.getName() +
      "' calls SQL compiler, MAL runtime, GDK, stream, or client API helper"
    or
    hasMemorySensitiveCallIn(f) and touchesMonetDbStateIn(f) and
    message = "MonetDB priority function '" + f.getName() +
      "' combines state metadata with memory-sensitive helper"
  )
select f, message
