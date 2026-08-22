/**
 * @name MariaDB PGQS descriptor and aggregate risk anchors
 * @description Hand-written Stage-1 Query A for MariaDB. It reports Stage-0
 *              priority SQL-layer functions that combine Item/Field/TABLE/JOIN
 *              descriptors, range/filesort/aggregate semantics, or local memory
 *              sinks. No LLM-generated QL is used.
 * @kind problem
 * @problem.severity warning
 * @id mariadb/pgqs-descriptor-aggregate-risk
 */

import cpp
import priority_functions

predicate isMariaDbSqlSource(Function f) {
  f.getFile().getRelativePath().regexpMatch(
    "(^|.*/)sql/(item|field|sql_|opt_|filesort|records|handler|table|" +
    "temporary_tables|json|sp_|sql_select|sql_parse|sql_base|sql_insert|" +
    "sql_update|sql_delete|sql_prepare|sql_type|sql_executor).*"
  )
}

predicate isMariaDbSqlDriver(Function f) {
  f.getName().regexpMatch(
    "^(dispatch_command|do_command|mysql_parse|mysql_execute_command|" +
    "mysql_execute_command_internal|mysql_select|handle_select|" +
    "mysql_insert|mysql_update|mysql_delete)$"
  )
}

predicate isMariaDbSemanticHotFunction(Function f) {
  f.getName().regexpMatch(
    "^(execute|execute_inner|optimize|optimize_inner|optimize_stage2|" +
    "fix_fields|resolve_type|val_int|val_real|val_str|val_decimal|val_bool|" +
    "save_in_field.*|store|reset_field|update_field|clear|add|" +
    "make_join_select|make_join_readinfo|do_select|sub_select.*|" +
    "join_read_.*|test_if_quick_select|test_quick_select|get_mm_tree|" +
    "get_func_mm_tree|filesort|create_sort_index|copy_funcs|" +
    "create_tmp_field|create_tmp_table|init_read_record)$"
  )
}

predicate isMariaDbPriorityContext(Function f) {
  isMariaDbSqlSource(f) and
  (isPriorityFunction(f) or isMariaDbSqlDriver(f) or isMariaDbSemanticHotFunction(f))
}

bindingset[name]
predicate hasDescriptorLikeName(string name) {
  name.regexpMatch(
    ".*(field|m_field|result_field|table|table_list|table_ref|tab|join|" +
    "key_part|key_info|ref_item|item|arg|args|column|cached|type_handler|" +
    "tmp_table|sort_field|sortorder|group|order|cond|where|quick|range|" +
    "sel_arg|record|null_value|decimals|maybe_null).*"
  )
}

predicate hasDescriptorLikeType(Variable v) {
  v.getType().toString().regexpMatch(
    ".*(Item|Field|TABLE|TABLE_LIST|TABLE_REF|JOIN|JOIN_TAB|KEY|" +
    "KEY_PART_INFO|SORT_FIELD|Filesort|ORDER|COND|SEL_ARG|SEL_TREE|" +
    "QUICK_SELECT|Copy_field|TMP_TABLE_PARAM|Type_handler|" +
    "Item_sum|Item_sum_avg|Cached_item|my_decimal|String).*"
  )
}

predicate touchesDescriptorIn(Function f) {
  exists(FieldAccess fa |
    fa.getEnclosingFunction() = f and
    hasDescriptorLikeName(fa.getTarget().getName())
  )
  or
  exists(VariableAccess va, Variable v |
    va.getEnclosingFunction() = f and
    va.getTarget() = v and
    (hasDescriptorLikeName(v.getName()) or hasDescriptorLikeType(v))
  )
}

predicate hasMemorySinkIn(Function f) {
  exists(FunctionCall call, Function target |
    call.getEnclosingFunction() = f and
    call.getTarget() = target and
    target.getName().regexpMatch(
      "^(memcpy|memmove|memset|my_malloc|my_realloc|my_multi_malloc|" +
      "alloc_root|multi_alloc_root|sql_alloc|thd_alloc|memdup_root|" +
      "strmake_root|my_strdup|my_strndup)$"
    )
  )
}

predicate hasDescriptorSemanticCallIn(Function f) {
  exists(FunctionCall call, Function target |
    call.getEnclosingFunction() = f and
    call.getTarget() = target and
    target.getName().regexpMatch(
      "^(field_type|real_type|result_type|cmp_type|type_handler|" +
      "Item_save_in_field|save_in_field.*|val_int|val_real|val_str|" +
      "val_decimal|fix_fields|resolve_type|reset_field|update_field|" +
      "copy_or_same|create_tmp_field|filesort|make_sortkey|" +
      "test_if_quick_select|test_quick_select|get_mm_tree|get_func_mm_tree|" +
      "copy_funcs|update_item_cache_if_changed)$"
    )
  )
}

predicate hasAggregateOrGroupingSignal(Function f) {
  f.getName().regexpMatch(
    "^(reset_field|update_field|clear|add|copy_or_same|create_tmp_field|" +
    "filesort|make_sortkey|end_write_group|end_send_group|setup_copy_fields)$"
  )
  or
  exists(FunctionCall call, Function target |
    call.getEnclosingFunction() = f and
    call.getTarget() = target and
    target.getName().regexpMatch(
      "^(reset_field|update_field|create_tmp_field|filesort|copy_funcs|" +
      "end_write_group|end_send_group|setup_copy_fields)$"
    )
  )
}

from Function f, string message
where
  isMariaDbPriorityContext(f) and
  (
    touchesDescriptorIn(f) and
    message = "MariaDB priority function '" + f.getName() +
      "' touches Item/Field/TABLE/JOIN descriptor metadata"
    or
    hasDescriptorSemanticCallIn(f) and
    message = "MariaDB priority function '" + f.getName() +
      "' calls type, grouping, range, or filesort semantic helper"
    or
    hasMemorySinkIn(f) and touchesDescriptorIn(f) and
    message = "MariaDB priority function '" + f.getName() +
      "' combines descriptor metadata with a memory-sensitive sink"
    or
    hasAggregateOrGroupingSignal(f) and touchesDescriptorIn(f) and
    message = "MariaDB priority function '" + f.getName() +
      "' combines aggregate/grouping state with descriptor metadata"
  )
select f, message
