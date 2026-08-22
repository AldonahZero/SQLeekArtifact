/**
 * MonetDB-specific SQL-reachable function pool for RQ4 w/o-M1.
 *
 * MonetDB's CodeQL database contains a very large number of C/C++ functions.
 * Restricting the SQL entry relation to MonetDB's public query paths keeps the
 * same SQL-reachability definition while avoiding the cross-product created by
 * generic entry-point names that are not present in MonetDB.
 *
 * @name MonetDB SQL-reachable function pool
 * @description Enumerate functions reachable from MonetDB SQL entry points.
 * @kind problem
 * @problem.severity warning
 * @id rq4/monetdb-sql-reachable-function-pool
 */
import cpp

class MonetSqlEntryPoint extends Function {
  MonetSqlEntryPoint() {
    this.getName().regexpMatch(
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
