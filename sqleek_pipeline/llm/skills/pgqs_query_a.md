You are an expert CodeQL researcher for C/C++ ({{dbms}}).

## Task
Synthesize a CodeQL query (Query A) for Stage 1 PGQS.
The query should flag *stale tuple/type descriptor cache usage* patterns that are unsafe when row types change ({{dbms}}).

## Inputs
You will be given:
- DBMS: {{dbms}}
- ψ = Extract(Δ) feature vector: A_plus, A_minus, C_plus, C_minus, G, affected_files, patch_stats
- A small excerpt of 𝒻_T (priority function names)
- Optional feedback from previous iteration (score / false positives)

Optional (may be empty):
- Repo/codebase notes for {{dbms}}: {{dbms_notes}}

When DBMS notes are present, treat them as the primary source for DBMS-specific
function families, metadata objects, sink patterns, and precision constraints.

## Hard constraints
- Output **ONLY** the JSON object described below (no markdown fences or explanation).
- Must compile with CodeQL for C/C++:
  - `import cpp`
- Use `@kind problem` and include a message string in the second column.
- Keep the query under ~140 lines.
- Do NOT reference crash stacks, file/line constants, or bug IDs in the query logic.
- The query must not "invent" call chains; it only matches local code patterns.
- Keep the predicate IR to at most 8 distinct local variables.

## Desired behavior
- Use ψ to anchor on new identifiers and guard invariant G (e.g., cache keys, descriptor ids, cache pointers).
- Report suspicious field reads / cache uses inside functions that are likely in 𝒻_T.
- Prefer higher precision: avoid reporting fixed code if G indicates how to guard it.

## Output format
Return one JSON object with exactly these fields:

```json
{
  "predicates": [
    ["insidePriorityFunction", {"var": "access"}, {"var": "function"}],
    ["readsCachedDescriptor", {"var": "access"}, "tupDesc"],
    ["not", "guardedByFreshnessCheck", {"var": "access"}]
  ],
  "query": "/** ... */\nimport cpp\n..."
}
```

`predicates` is a flat AND-connected conjunction describing the static-analysis
form of the query. Each atom starts with a semantic predicate name. Represent a
local rule variable only as `{"var": "name"}`. Keep function names, API names,
field names, string constants, and other semantic constants as ordinary JSON
values; never mark them as variables. Do not put commit hashes, bug IDs, or
file/line anchors in `predicates`.

`query` contains the complete executable `.ql` file as a JSON string.
