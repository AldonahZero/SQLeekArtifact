/**
 * SQL-reachable function pool for the RQ4 w/o-M1 ablation.
 *
 * This query deliberately does not import priority_functions. Its only
 * filter is reachability from the same SQL-facing entry points used by the
 * Stage-1 callchain query. The pool is a set of functions, so reachability is
 * tested existentially and the query emits one row per function rather than
 * enumerating every entrypoint/function pair. This preserves the target-pool
 * semantics while avoiding a large duplicate transitive-closure relation.
 * Source filtering is kept in the query so external/generated code does not
 * inflate the reachability result set. The ablation does not use reachability
 * depth to rank targets, so the emitted neutral depth is 1.
 *
 * @name SQL-reachable function pool
 * @description Enumerate functions reachable from SQL entry points.
 * @kind problem
 * @problem.severity warning
 * @id rq4/sql-reachable-function-pool
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

from Function f
where
  sourceCandidate(f) and
  exists(SqlEntryPoint entry | entry != f and entry.calls+(f))
select
  f,
  "entry=sql_reachable" +
    ";function=" + f.getName() +
    ";file=" + f.getFile().getRelativePath() +
    ";start=" + f.getLocation().getStartLine() +
    ";end=" + f.getLocation().getEndLine() +
    ";depth=1"
