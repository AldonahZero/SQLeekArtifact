/**
 * @name DBMS tainted size in memory operation (priority-filtered)
 * @description SQL/parser-related source may flow to memory op size argument.
 *              Sink reported only when the call sits in a Stage-0 priority function.
 * @kind path-problem
 * @problem.severity error
 * @id dbms/tainted-memory-sink-priority
 */
import cpp
import semmle.code.cpp.ir.dataflow.TaintTracking
import priority_functions

class SqlParserSource extends DataFlow::Node {
  SqlParserSource() {
    exists(Function f |
      f.getName()
        .regexpMatch(
          "pg_strtoint.*|scanint.*|DirectFunctionCall.*|" +
            "GetAttributeByNum|heap_getattr|slot_getattr|" +
            "sqlite3GetVarint.*|sqlite3Read.*|getVarint.*|" +
            "my_strtoll.*|my_strntoll.*|my_strntoull.*|" +
            "mysql_parse|dispatch_command|do_command|mysql_execute_command|" +
            ".*[Rr]ead.*[Ii]nt.*|.*[Pp]arse.*[Ll]en.*"
        ) and
      this.asExpr().(Call).getTarget() = f
    )
  }
}

class MemoryOpSink extends DataFlow::Node {
  string sinkDesc;

  MemoryOpSink() {
    exists(Call c, Function f, int argIdx |
      c.getTarget() = f and
      this.asExpr() = c.getArgument(argIdx) and
      (
        f.getName().regexpMatch("memcpy|memmove|memset") and argIdx = 2
        or
        f.getName() = "text_to_cstring" and argIdx = 0
        or
        f.getName().regexpMatch("palloc|palloc0") and argIdx = 0
        or
        f.getName() = "repalloc" and argIdx = 1
        or
        f.getName().regexpMatch("sqlite3DbMallocRaw|sqlite3DbMallocZero|sqlite3Malloc") and argIdx = 0
        or
        f.getName().regexpMatch("my_malloc|my_realloc") and argIdx = 0
        or
        f.getName().regexpMatch("alloc_root|multi_alloc_root") and argIdx = 1
        or
        f.getName() = "sql_alloc" and argIdx = 0
        or
        f.getName().regexpMatch("memdup_root|strmake_root") and argIdx = 2
      ) and
      sinkDesc = f.getName() and
      exists(Function enc | enc = c.getEnclosingFunction() | isPriorityFunction(enc))
    )
  }

  string getDesc() { result = sinkDesc }
}

module Config implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) { source instanceof SqlParserSource }

  predicate isSink(DataFlow::Node sink) { sink instanceof MemoryOpSink }

  predicate observeDiffInformedIncrementalMode() { any() }
}

module TaintDbms = TaintTracking::Global<Config>;

import TaintDbms::PathGraph

from TaintDbms::PathNode psource, TaintDbms::PathNode psink
where
  TaintDbms::flowPath(psource, psink) and
  psink.getNode() instanceof MemoryOpSink
select psink.getNode().asExpr(), psource, psink,
  "SQL/parser input flows to memory op '" + psink.getNode().(MemoryOpSink).getDesc() +
    "' (priority-filtered sink)",
  psource.getNode(), psource.getNode().toString()
