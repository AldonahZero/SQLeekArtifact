/**
 * MonetDB primary SQL-reachable function pool for RQ4 w/o-M1.
 *
 * The pool is the source-level closure reachable from MonetDB's public
 * monetdbe_query entry point.  Using one concrete SQL API keeps each function
 * represented once in the result while preserving the ablation's uniform
 * sampling rule and the unchanged downstream M2/M3 pipeline.
 *
 * @name MonetDB primary SQL-reachable function pool
 * @description Enumerate functions reachable from monetdbe_query.
 * @kind problem
 * @problem.severity warning
 * @id rq4/monetdb-primary-sql-reachable-function-pool
 */
import cpp

class MonetSqlEntryPoint extends Function {
  MonetSqlEntryPoint() {
    this.getName() = "monetdbe_query"
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

from MonetSqlEntryPoint entry, Function f
where
  entry != f and
  sourceCandidate(f) and
  entry.calls+(f)
select
  f,
  "entry=" + entry.getName() +
    ";function=" + f.getName() +
    ";file=" + f.getFile().getRelativePath() +
    ";start=" + f.getLocation().getStartLine() +
    ";end=" + f.getLocation().getEndLine() +
    ";depth=1"
