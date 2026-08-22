/**
 * @name MySQL PGQS descriptor and metadata risk anchors
 * @description Reports MySQL SQL-layer priority functions that combine Item/Field/TABLE metadata, descriptor-sensitive semantic calls, or local memory sinks.
 * @kind problem
 * @problem.severity warning
 * @id mysql/pgqs-descriptor-risk
 */

import cpp
import priority_functions

predicate isMysqlSqlSource(Function f) {
  f.getFile().getRelativePath().regexpMatch(
    "(^|.*/)sql/(item|field|sql_executor|sql_select|sql_parse|sql_base|sql_tmp_table|" +
    "sql_insert|sql_update|sql_delete|filesort|handler|range_optimizer/.*|join_optimizer/.*).*"
  )
}

predicate isMysqlSqlDriver(Function f) {
  f.getName().regexpMatch(
    "^(dispatch_command|dispatch_sql_command|do_command|mysql_parse|mysql_execute_command|" +
    "execute_sqlcom_select|handle_query)$"
  )
}

predicate isMysqlSemanticHotFunction(Function f) {
  f.getName().regexpMatch(
    "^(fix_fields|resolve_type|resolve_type_inner|propagate_type|do_itemize|" +
    "save_in_field|save_in_field_inner|store_value|make_field|set_field|reset_field|" +
    "get_mm_tree|test_quick_select|make_join_readinfo|filesort|create_sort_index|" +
    "copy_inner|copy_data|bind_fields|join_free|item_init|cleanup_items|" +
    "execute|execute_inner|optimize|prepare_inner)$"
  )
}

predicate isMysqlPriorityContext(Function f) {
  isMysqlSqlSource(f) and
  (isPriorityFunction(f) or isMysqlSqlDriver(f) or isMysqlSemanticHotFunction(f))
}

bindingset[name]
predicate hasDescriptorLikeName(string name) {
  name.regexpMatch(
    ".*(field|m_field|result_field|table|m_table_ref|table_ref|key_part|key_info|" +
    "ref_item|item_name|column_info|cached_field_type|access_path|join_tab|qep_tab|" +
    "read_set|write_set|tmp_table|sort_field).*"
  )
}

predicate hasDescriptorLikeType(Variable v) {
  v.getType().toString().regexpMatch(
    ".*(Item|Field|TABLE|TABLE_LIST|TABLE_REF|Table_ref|KEY_PART_INFO|" +
    "AccessPath|JOIN_TAB|QEP_TAB|Copy_field|Field_iterator).*"
  )
}

predicate isDescriptorAccessIn(Function f) {
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
      "^(memcpy|memmove|memset|my_malloc|my_realloc|alloc_root|multi_alloc_root|" +
      "sql_alloc|memdup_root|strmake_root|my_strndup|my_strdup)$"
    )
  )
}

predicate hasDescriptorSemanticCallIn(Function f) {
  exists(FunctionCall call, Function target |
    call.getEnclosingFunction() = f and
    call.getTarget() = target and
    target.getName().regexpMatch(
      "^(field_type|real_type|result_type|data_type|cmp_type|item_cmp_type|" +
      "type_conversion_status_to_store_key|find_item.*|collect_item_field.*|" +
      "setup_semijoin_dups_elimination|init_join_cache|set_semijoin_info)$"
    )
  )
}

from Function f, string message
where
  isMysqlPriorityContext(f) and
  (
    isDescriptorAccessIn(f) and
    message = "MySQL PGQS priority function '" + f.getName() +
      "' touches Item/Field/TABLE descriptor metadata"
    or
    hasDescriptorSemanticCallIn(f) and
    message = "MySQL PGQS priority function '" + f.getName() +
      "' calls descriptor/type-resolution helper"
    or
    hasMemorySinkIn(f) and isDescriptorAccessIn(f) and
    message = "MySQL PGQS priority function '" + f.getName() +
      "' combines descriptor metadata with a memory-sensitive sink"
  )
select f, message
