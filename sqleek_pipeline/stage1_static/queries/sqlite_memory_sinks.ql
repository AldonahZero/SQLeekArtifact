/**
 * @name SQLite priority local memory sinks
 * @description Lightweight SQLite memory sink query. It reports local allocation,
 *              copy, Mem growth/copy, varint, btree payload, and record-decode
 *              operations inside Stage-0 priority functions without running
 *              global taint flow.
 * @kind problem
 * @problem.severity warning
 * @id dbms/sqlite/local-memory-sinks
 */
import cpp
import priority_functions

predicate isSqlitePriorityContext(Function f) {
  isPriorityFunction(f)
  or
  f.getName().regexpMatch(
    "sqlite3_exec|sqlite3_prepare.*|sqlite3_step|sqlite3VdbeExec|" +
    "sqlite3GetVarint.*|sqlite3Read.*|getVarint.*|sqlite3VdbeMem.*|" +
    "sqlite3VdbeSerial.*|sqlite3VdbeRecordCompare.*|sqlite3Btree.*|" +
    "sqlite3Where.*|vdbeRecordDecodeInt|sqlite3MemCompare"
  )
}

class SqliteMemorySinkCall extends FunctionCall {
  string targetName;

  SqliteMemorySinkCall() {
    exists(Function target, Function enclosing, int argIdx |
      this.getTarget() = target and
      this.getEnclosingFunction() = enclosing and
      isSqlitePriorityContext(enclosing) and
      this.getArgument(argIdx) = this.getArgument(argIdx) and
      (
        target.getName().regexpMatch("memcpy|memmove|memset") and argIdx = 2
        or
        target.getName().regexpMatch(
          "sqlite3DbMallocRaw|sqlite3DbMallocRawNN|sqlite3DbMallocZero|" +
          "sqlite3Malloc|sqlite3MallocZero|sqlite3_realloc64|sqlite3Realloc"
        ) and argIdx = 0
        or
        target.getName().regexpMatch("sqlite3VdbeMemGrow|sqlite3VdbeMemSetStr") and argIdx = 1
      ) and
      targetName = target.getName()
    )
  }

  string getTargetName() { result = targetName }
}

class SqliteDecodeCall extends FunctionCall {
  string targetName;

  SqliteDecodeCall() {
    exists(Function target, Function enclosing |
      this.getTarget() = target and
      this.getEnclosingFunction() = enclosing and
      isSqlitePriorityContext(enclosing) and
      target.getName().regexpMatch(
        "sqlite3GetVarint.*|sqlite3Read.*|getVarint.*|sqlite3VdbeSerialGet|" +
        "sqlite3VdbeSerialType|vdbeRecordDecodeInt|sqlite3VdbeRecordCompare.*|" +
        "sqlite3MemCompare|sqlite3VdbeMemFromBtree.*|sqlite3BtreePayload.*"
      ) and
      targetName = target.getName()
    )
  }

  string getTargetName() { result = targetName }
}

from Expr e, string message
where
  exists(SqliteMemorySinkCall call |
    e = call and
    message = "SQLite priority function reaches local memory sink '" + call.getTargetName() + "'"
  )
  or
  exists(SqliteDecodeCall call |
    e = call and
    message = "SQLite priority function uses record/varint/btree decode helper '" +
      call.getTargetName() + "'"
  )
select e, message
