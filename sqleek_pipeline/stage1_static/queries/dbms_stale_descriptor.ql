/**
 * @name DBMS stale type descriptor access
 * @description Flags cached TupleDesc / type-related fields read inside evaluation
 *              or output paths where DDL may change layout (ExprEvalRowtypeCache class of issues).
 * @kind problem
 * @severity warning
 * @id dbms/stale-descriptor
 * @tags security dbms postgresql
 */
import cpp

/** Field names that often hold cached tuple/type descriptors. */
class CachedDescriptorFieldAccess extends FieldAccess {
  CachedDescriptorFieldAccess() {
    this.getTarget()
        .getName()
        .regexpMatch(
          "tupdesc|Tupdesc|tupDesc|typeDesc|typentry|cacheptr|tupdesc_id|" +
            "my_extra|column_info|finfo|flinfo|field|m_field|result_field|" +
            "table|m_table_ref|cached_field_type"
        )
  }
}

/** Hot consumers along composite/text output and row evaluation paths. */
class DescriptorConsumerFunction extends Function {
  DescriptorConsumerFunction() {
    this.getName()
      .regexpMatch(
        "ExecEvalRow|record_out|textout|.*[Oo]utput[Ff]unction.*|" +
          ".*[Pp]rint[Tt]up.*|.*[Ff]etch.*[Tt]uple.*|" +
          "ExecEvalFieldSelect|ExecEvalConvertRowtype|" +
          "mysql_execute_command|dispatch_command|mysql_parse|" +
          "save_in_field|copy_inner|type_conversion_status_to_store_key|" +
          "test_quick_select|get_mm_tree|filesort|create_sort_index"
      )
  }
}

from CachedDescriptorFieldAccess access, DescriptorConsumerFunction consumer
where access.getEnclosingFunction() = consumer
select access,
  "Cached descriptor field '" + access.getTarget().getName() + "' in '" + consumer.getName() +
    "' — review invalidation after DDL (stale TupleDesc / rowtype cache pattern)."
