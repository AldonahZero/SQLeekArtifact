/**
 * @name DBMS call chain: SQL entry → priority danger function
 * @description Transitive calls from SQL execution entry points to priority-scored
 *              or memory-danger functions (Stage 0 + small builtin sink set).
 * @kind problem
 * @problem.severity recommendation
 * @id dbms/callchain-priority
 */
import cpp
import priority_functions

class SqlEntryPoint extends Function {
  SqlEntryPoint() {
    this.getName()
      .regexpMatch(
        "exec_simple_query|PortalRun|PortalRunSelect|ExecutorRun|" +
          "ExecProcNode|ExecFetch|RunFromStore|" +
          // Also include key mid-stack DBMS executors/output fns to improve callgraph coverage
          // when direct SQL entry →* target edges are missing due to indirection.
          "printtup|OutputFunctionCall|record_out|textout|" +
          "sqlite3_exec|sqlite3_prepare.*|sqlite3_step|sqlite3VdbeExec|" +
          "handle_connection|do_command|dispatch_command|" +
          "mysql_parse|mysql_execute_command|mysql_execute|" +
          "monetdbe_query|monetdbe_query_internal|monetdbe_query_remote|" +
          "SQLengine|SQLengine_|SQLparser|SQLparser_body"
      )
  }
}

class PriorityDangerFunction extends Function {
  PriorityDangerFunction() {
    isPriorityFunction(this)
    or
    this.getName().regexpMatch(
      "ExecEvalRow|record_out|text_to_cstring|textout|" +
        "printtup|OutputFunctionCall|FunctionCall1Coll|" +
        "ExecEvalFieldSelect|ExecEvalConvertRowtype|" +
        "heap_form_tuple|heap_deform_tuple|" +
        "palloc|repalloc|memcpy|memmove|" +
        "my_malloc|my_realloc|alloc_root|sql_alloc|memdup_root|strmake_root|" +
        "save_in_field|copy_inner|type_conversion_status_to_store_key|" +
        "test_quick_select|get_mm_tree|filesort|create_sort_index|" +
        "SQLparser|SQLengine|SQLengine_|rel_unnest_dependent|push_up_join|" +
        "run_exp_rewriter|runMALsequence|MALrun|mal_interpreter|" +
        "BATjoin|BATappend|BATselect|GDKmalloc|GDKrealloc|GDKstrdup|" +
        "bs_write|write_out|monetdbe_query_internal"
    )
  }
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

from SqlEntryPoint entry, PriorityDangerFunction danger, int mindepth
where
  mindepth =
    min(int depth |
      callsTransitive(entry, danger, depth)
    | depth)
select entry,
  "depth=" + mindepth + " " + entry.getName() + " →* " + danger.getName(),
  danger,
  "depth=" + mindepth
