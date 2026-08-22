/**
 * MonetDB direct SQL-reachable function pool for RQ4 w/o-M1.
 *
 * This query selects source functions directly called by monetdbe_query.  It
 * is intentionally finite and auditable after the full transitive query was
 * found to exhaust the CodeQL memory-mapped relation on the MonetDB database.
 *
 * @name MonetDB direct SQL-reachable function pool
 * @description Enumerate functions directly called from monetdbe_query.
 * @kind problem
 * @problem.severity warning
 * @id rq4/monetdb-depth1-sql-reachable-function-pool
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
  entry.calls(f)
select
  f,
  "entry=" + entry.getName() +
    ";function=" + f.getName() +
    ";file=" + f.getFile().getRelativePath() +
    ";start=" + f.getLocation().getStartLine() +
    ";end=" + f.getLocation().getEndLine() +
    ";depth=1"
