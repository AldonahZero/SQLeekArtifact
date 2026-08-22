/**
 * @name MonetDB priority local memory sinks
 * @description Lightweight MonetDB memory sink query. It reports local copy,
 *              allocation, exception-buffer, BAT/GDK, stream, and embedded API
 *              operations inside Stage-0 priority functions without running
 *              global taint flow.
 * @kind problem
 * @problem.severity warning
 * @id dbms/monetdb/local-memory-sinks
 */
import cpp
import priority_functions

predicate isMonetDbPriorityContext(Function f) {
  isPriorityFunction(f)
  or
  f.getName().regexpMatch(
    "SQLparser|SQLparser_body|SQLengine|SQLengine_|SQLprepare|" +
    "SQLexecutePrepared|monetdbe_query.*|rel_.*|exp_.*|mvc_.*|" +
    "push_up_.*|run_exp_rewriter|runMALsequence|MALrun|mal_.*|" +
    "BAT.*|GDK.*|BBP.*|bs_.*|stream_.*|write_out"
  )
}

class MonetDbMemorySinkCall extends FunctionCall {
  string targetName;

  MonetDbMemorySinkCall() {
    exists(Function target, Function enclosing, int argIdx |
      this.getTarget() = target and
      this.getEnclosingFunction() = enclosing and
      isMonetDbPriorityContext(enclosing) and
      this.getArgument(argIdx) = this.getArgument(argIdx) and
      (
        target.getName().regexpMatch("memcpy|memmove|memset") and argIdx = 2
        or
        target.getName().regexpMatch(
          "malloc|calloc|realloc|GDKmalloc|GDKzalloc|GDKrealloc|" +
          "GDKstrdup|GDKstrndup|sa_alloc|sa_zalloc|sa_realloc|sa_strdup"
        ) and argIdx = 0
        or
        target.getName().regexpMatch("HEAPalloc|HEAPextend|BATextend") and argIdx >= 0
      ) and
      targetName = target.getName()
    )
  }

  string getTargetName() { result = targetName }
}

class MonetDbStateAccess extends FieldAccess {
  MonetDbStateAccess() {
    exists(Function enclosing |
      this.getEnclosingFunction() = enclosing and
      isMonetDbPriorityContext(enclosing) and
      this.getTarget()
        .getName()
        .regexpMatch(
          "mvc|rel|exp|stmt|sql|query|client|cntxt|backend|be|sa|" +
          "mal|mb|stk|pci|blk|bat|BAT|bbp|heap|tail|head|column|type|" +
          "schema|stream|buf|buffer|blob|result|cursor|descriptor|param"
        )
    )
  }
}

class MonetDbSemanticSinkCall extends FunctionCall {
  string targetName;

  MonetDbSemanticSinkCall() {
    exists(Function target, Function enclosing |
      this.getTarget() = target and
      this.getEnclosingFunction() = enclosing and
      isMonetDbPriorityContext(enclosing) and
      target.getName().regexpMatch(
        "SQLparser|SQLparser_body|SQLengine|SQLengine_|runMALsequence|" +
        "rel_.*|exp_.*|push_up_.*|run_exp_rewriter|BAT.*|GDK.*|" +
        "bs_.*|stream_.*|write_out|monetdbe_.*"
      ) and
      targetName = target.getName()
    )
  }

  string getTargetName() { result = targetName }
}

from Expr e, string message
where
  exists(MonetDbMemorySinkCall call |
    e = call and
    message = "MonetDB priority function reaches local memory sink '" +
      call.getTargetName() + "'"
  )
  or
  exists(MonetDbStateAccess access |
    e = access and
    message = "MonetDB priority function reads SQL/MAL/BAT/stream state '" +
      access.getTarget().getName() + "'"
  )
  or
  exists(MonetDbSemanticSinkCall call |
    e = call and
    message = "MonetDB priority function reaches SQL/MAL/GDK/stream helper '" +
      call.getTargetName() + "'"
  )
select e, message
