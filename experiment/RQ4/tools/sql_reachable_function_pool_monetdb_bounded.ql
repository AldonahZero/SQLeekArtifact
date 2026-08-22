/**
 * MonetDB bounded SQL-reachable function pool for RQ4 w/o-M1.
 *
 * This follows the Stage-1 call-chain depth bound (15) and starts at the
 * public monetdbe_query SQL API.  The Python preparation step uniformly
 * samples K distinct source functions from this bounded SQL-reachable pool.
 *
 * @name MonetDB bounded SQL-reachable function pool
 * @description Enumerate functions reachable from monetdbe_query within 15 calls.
 * @kind problem
 * @problem.severity warning
 * @id rq4/monetdb-bounded-sql-reachable-function-pool
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

predicate callsTransitive(Function caller, Function callee, int depth) {
  depth = 0 and caller = callee
  or
  depth = 1 and caller.calls(callee)
  or
  depth <= 15 and
  exists(Function mid |
    caller.calls(mid) and
    callsTransitive(mid, callee, depth - 1)
  )
}

from MonetSqlEntryPoint entry, Function f, int mindepth
where
  entry != f and
  sourceCandidate(f) and
  mindepth = min(int depth | callsTransitive(entry, f, depth) | depth)
select
  f,
  "entry=" + entry.getName() +
    ";function=" + f.getName() +
    ";file=" + f.getFile().getRelativePath() +
    ";start=" + f.getLocation().getStartLine() +
    ";end=" + f.getLocation().getEndLine() +
    ";depth=" + mindepth
