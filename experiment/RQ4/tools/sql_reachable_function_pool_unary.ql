/**
 * SQL-reachable function pool for the RQ4 w/o-M1 ablation.
 *
 * This is the set-valued formulation of the SQL reachability pool.  It uses
 * one recursive unary predicate rooted at all SQL-facing entry points rather
 * than materializing an entrypoint/function transitive-closure relation.
 * Consequently each reachable function is emitted once while preserving the
 * same source filter and the same unbounded calls+ reachability semantics.
 * The ablation does not use reachability depth to rank targets, so the
 * emitted neutral depth is 1.
 *
 * @name SQL-reachable function pool (unary)
 * @description Enumerate functions reachable from SQL entry points.
 * @kind problem
 * @problem.severity warning
 * @id rq4/sql-reachable-function-pool-unary
 */
import cpp

class SqlEntryPoint extends Function {
  SqlEntryPoint() {
    this.getName().regexpMatch(
      "exec_simple_query|PortalRun|PortalRunSelect|ExecutorRun|" +
        "ExecProcNode|ExecFetch|RunFromStore|" +
        "printtup|OutputFunctionCall|record_out|textout|" +
        "sqlite3_exec|sqlite3_prepare.*|sqlite3_step|sqlite3VdbeExec|" +
        "handle_connection|do_command|dispatch_command|" +
        "mysql_parse|mysql_execute_command|mysql_execute|" +
        "monetdbe_query|monetdbe_query_internal|monetdbe_query_remote|" +
        "SQLengine|SQLengine_|SQLparser|SQLparser_body"
    )
  }
}

predicate sourceCandidate(Function f) {
  f.getFile().getRelativePath().regexpMatch(".*\\.(c|cc|cpp|cxx)$") and
  not f.getFile().getRelativePath().regexpMatch(
    "(^|/)(build|builds|generated|third_party|vendor|client|clients|extra|" +
      "test|tests|unittest|examples|example|bench|benchmark|doc|docs|" +
      "scripts|support-files|packaging|debian|win|windows)(/|$)"
  )
}

/** One or more call edges from any SQL-facing entry point. */
predicate sqlReachable(Function f) {
  exists(SqlEntryPoint entry | entry.calls(f)) or
  exists(Function caller | sqlReachable(caller) and caller.calls(f))
}

from Function f
where
  sourceCandidate(f) and
  sqlReachable(f)
select
  f,
  "entry=sql_reachable" +
    ";function=" + f.getName() +
    ";file=" + f.getFile().getRelativePath() +
    ";start=" + f.getLocation().getStartLine() +
    ";end=" + f.getLocation().getEndLine() +
    ";depth=1"
