/**
 * @name MariaDB priority local memory sinks
 * @description Lightweight MariaDB memory sink query. It avoids global taint flow
 *              and reports local memory/copy/allocation, type conversion, range,
 *              filesort, aggregate, and metadata access patterns inside Stage-0
 *              priority functions.
 * @kind problem
 * @problem.severity warning
 * @id dbms/mariadb/local-memory-sinks
 */
import cpp
import priority_functions

predicate isMariaDbPriorityContext(Function f) {
  isPriorityFunction(f)
  or
  f.getName().regexpMatch(
    "mysql_execute_command|dispatch_command|do_command|mysql_parse|" +
    "save_in_field.*|copy_inner|get_mm_tree|get_func_mm_tree|" +
    "test_quick_select|test_if_quick_select|filesort|make_sortkey|" +
    "val_int|val_real|val_str|val_decimal|fix_fields|optimize|" +
    "execute|execute_inner|copy|reset_field|update_field|create_tmp_field"
  )
}

class MariaDbMemorySinkCall extends FunctionCall {
  string targetName;

  MariaDbMemorySinkCall() {
    exists(Function target, Function enclosing, int argIdx |
      this.getTarget() = target and
      this.getEnclosingFunction() = enclosing and
      isMariaDbPriorityContext(enclosing) and
      this.getArgument(argIdx) = this.getArgument(argIdx) and
      (
        target.getName().regexpMatch("memcpy|memmove|memset") and argIdx = 2
        or
        target.getName().regexpMatch("my_malloc|my_realloc|my_multi_malloc") and argIdx = 1
        or
        target.getName().regexpMatch("alloc_root|multi_alloc_root") and argIdx = 1
        or
        target.getName().regexpMatch("sql_alloc|thd_alloc") and argIdx = 0
        or
        target.getName().regexpMatch("memdup_root|strmake_root|my_strdup|my_strndup") and argIdx = 2
      ) and
      targetName = target.getName()
    )
  }

  string getTargetName() { result = targetName }
}

class MariaDbMetadataAccess extends FieldAccess {
  MariaDbMetadataAccess() {
    exists(Function enclosing |
      this.getEnclosingFunction() = enclosing and
      isMariaDbPriorityContext(enclosing) and
      this.getTarget()
        .getName()
        .regexpMatch(
          "field|m_field|result_field|table|table_list|m_table_ref|column_info|" +
          "cached_field_type|key_part|ref_item|item|join|quick|range|sort_field|" +
          "group|order|null_value|decimals|type_handler"
        )
    )
  }
}

class MariaDbSemanticSinkCall extends FunctionCall {
  string targetName;

  MariaDbSemanticSinkCall() {
    exists(Function target, Function enclosing |
      this.getTarget() = target and
      this.getEnclosingFunction() = enclosing and
      isMariaDbPriorityContext(enclosing) and
      target.getName().regexpMatch(
        "save_in_field.*|Item_save_in_field|reset_field|update_field|" +
        "create_tmp_field|filesort|copy_funcs|make_sortkey|val_decimal|" +
        "get_mm_tree|get_func_mm_tree|test_if_quick_select|test_quick_select"
      ) and
      targetName = target.getName()
    )
  }

  string getTargetName() { result = targetName }
}

from Expr e, string message
where
  exists(MariaDbMemorySinkCall call |
    e = call and
    message = "MariaDB priority function reaches local memory sink '" + call.getTargetName() + "'"
  )
  or
  exists(MariaDbMetadataAccess access |
    e = access and
    message = "MariaDB priority function reads table/field/query metadata '" +
      access.getTarget().getName() + "'"
  )
  or
  exists(MariaDbSemanticSinkCall call |
    e = call and
    message = "MariaDB priority function reaches type/range/filesort/aggregate helper '" +
      call.getTargetName() + "'"
  )
select e, message
