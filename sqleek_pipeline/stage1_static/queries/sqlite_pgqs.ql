/**
 * @name SQLite PGQS record and VDBE risk anchors
 * @description Hand-written Stage-1 Query A for SQLite. It reports Stage-0
 *              priority functions in VDBE, record decoding, planner, btree,
 *              and Mem-value paths that touch descriptor-like state or memory
 *              sensitive helpers.
 * @kind problem
 * @problem.severity warning
 * @id sqlite/pgqs-record-vdbe-risk
 */

import cpp
import priority_functions

predicate isSqliteSource(Function f) {
  f.getFile().getRelativePath().regexpMatch(
    "(^|.*/)(src/(vdbe|vdbeaux|vdbemem|vdbesort|where|btree|expr|select|" +
    "build|insert|update|delete|func|json|prepare|resolve).*|" +
    "ext/(fts5|session)/.*)"
  )
}

predicate isSqliteSqlDriver(Function f) {
  f.getName().regexpMatch(
    "^(sqlite3_exec|sqlite3_prepare.*|sqlite3_step|sqlite3VdbeExec|" +
    "sqlite3RunParser|sqlite3Select|sqlite3Where.*|sqlite3Insert|" +
    "sqlite3Update|sqlite3DeleteFrom)$"
  )
}

predicate isSqliteSemanticHotFunction(Function f) {
  f.getName().regexpMatch(
    "^(sqlite3Vdbe.*|vdbe.*|sqlite3Where.*|sqlite3Expr.*|sqlite3Select|" +
    "sqlite3Btree.*|sqlite3VdbeSerial.*|sqlite3GetVarint.*|" +
    "sqlite3MemCompare|sqlite3VdbeRecordCompare.*|sqlite3VdbeMem.*|" +
    "sqlite3DbMalloc.*|sqlite3Malloc.*|sqlite3Realloc.*)$"
  )
}

predicate isSqlitePriorityContext(Function f) {
  isSqliteSource(f) and
  (isPriorityFunction(f) or isSqliteSqlDriver(f) or isSqliteSemanticHotFunction(f))
}

bindingset[name]
predicate hasSqliteStateName(string name) {
  name.regexpMatch(
    ".*(pMem|aMem|apArg|pIn[0-9]*|pOut|pC|pCx|pCur|pKeyInfo|pFrame|" +
    "pBt|pBtx|pParse|pExpr|pSelect|pTab|pIdx|pOp|serial|Serial|hdr|" +
    "payload|Payload|nField|nHdr|szHdr|flags|enc|zMalloc|affinity|" +
    "coll|Coll|unpacked|Unpacked|record|Record).*"
  )
}

predicate hasSqliteStateType(Variable v) {
  v.getType().toString().regexpMatch(
    ".*(Mem|Vdbe|VdbeCursor|VdbeFrame|BtCursor|Btree|BtShared|" +
    "UnpackedRecord|KeyInfo|CollSeq|Expr|Select|Table|Index|Parse|" +
    "SorterRecord|SubProgram).*"
  )
}

predicate touchesSqliteStateIn(Function f) {
  exists(FieldAccess fa |
    fa.getEnclosingFunction() = f and
    hasSqliteStateName(fa.getTarget().getName())
  )
  or
  exists(VariableAccess va, Variable v |
    va.getEnclosingFunction() = f and
    va.getTarget() = v and
    (hasSqliteStateName(v.getName()) or hasSqliteStateType(v))
  )
}

predicate hasRecordDecodeCallIn(Function f) {
  exists(FunctionCall call, Function target |
    call.getEnclosingFunction() = f and
    call.getTarget() = target and
    target.getName().regexpMatch(
      "^(sqlite3VdbeSerialGet|sqlite3VdbeSerialType|sqlite3GetVarint.*|" +
      "vdbeRecordDecodeInt|sqlite3VdbeRecordCompare.*|sqlite3MemCompare|" +
      "sqlite3VdbeIdxRowid|sqlite3VdbeMemFromBtree.*)$"
    )
  )
}

predicate hasMemorySensitiveCallIn(Function f) {
  exists(FunctionCall call, Function target |
    call.getEnclosingFunction() = f and
    call.getTarget() = target and
    target.getName().regexpMatch(
      "^(memcpy|memmove|memset|sqlite3DbMalloc.*|sqlite3Malloc.*|" +
      "sqlite3Realloc.*|sqlite3VdbeMemGrow|sqlite3VdbeMemSetStr|" +
      "sqlite3VdbeMemCopy|sqlite3VdbeMemMove|sqlite3VdbeMemShallowCopy|" +
      "sqlite3VdbeMemRelease.*)$"
    )
  )
}

from Function f, string message
where
  isSqlitePriorityContext(f) and
  (
    touchesSqliteStateIn(f) and
    message = "SQLite priority function '" + f.getName() +
      "' touches VDBE/record/btree descriptor state"
    or
    hasRecordDecodeCallIn(f) and
    message = "SQLite priority function '" + f.getName() +
      "' reaches record or varint decode helper"
    or
    hasMemorySensitiveCallIn(f) and touchesSqliteStateIn(f) and
    message = "SQLite priority function '" + f.getName() +
      "' combines VDBE state with memory-sensitive helper"
  )
select f, message
