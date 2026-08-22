/**
 * @name MySQL priority local memory sinks
 * @description Lightweight MySQL memory sink query. It reports memory/copy/allocation
 *              calls and metadata accesses inside Stage-0 priority functions without
 *              running global taint flow.
 * @kind problem
 * @problem.severity warning
 * @id dbms/mysql/local-memory-sinks
 */
import cpp
import priority_functions

predicate isMysqlPriorityContext(Function f) {
  isPriorityFunction(f)
  or
  f.getName().regexpMatch(
    "mysql_execute_command|dispatch_command|do_command|mysql_parse|" +
      "save_in_field|copy_inner|get_mm_tree|test_quick_select|filesort|" +
      "val_int|val_real|fix_fields|optimize|copy"
  )
}

class MysqlMemorySinkCall extends FunctionCall {
  string targetName;

  MysqlMemorySinkCall() {
    exists(Function target, Function enclosing, int argIdx |
      this.getTarget() = target and
      this.getEnclosingFunction() = enclosing and
      isMysqlPriorityContext(enclosing) and
      this.getArgument(argIdx) = this.getArgument(argIdx) and
      (
        target.getName().regexpMatch("memcpy|memmove|memset") and argIdx = 2
        or
        target.getName().regexpMatch("my_malloc|my_realloc") and argIdx = 0
        or
        target.getName().regexpMatch("alloc_root|multi_alloc_root") and argIdx = 1
        or
        target.getName() = "sql_alloc" and argIdx = 0
        or
        target.getName().regexpMatch("memdup_root|strmake_root") and argIdx = 2
      ) and
      targetName = target.getName()
    )
  }

  string getTargetName() { result = targetName }
}

class MysqlMetadataAccess extends FieldAccess {
  MysqlMetadataAccess() {
    exists(Function enclosing |
      this.getEnclosingFunction() = enclosing and
      isMysqlPriorityContext(enclosing) and
      this.getTarget()
        .getName()
        .regexpMatch("field|m_field|result_field|table|m_table_ref|column_info|cached_field_type|key_part|ref_item|item")
    )
  }
}

from Expr e, string message
where
  exists(MysqlMemorySinkCall call |
    e = call and
    message = "MySQL priority function reaches local memory sink '" + call.getTargetName() + "'"
  )
  or
  exists(MysqlMetadataAccess access |
    e = access and
    message = "MySQL priority function reads table/field metadata '" +
      access.getTarget().getName() + "'"
  )
select e, message
