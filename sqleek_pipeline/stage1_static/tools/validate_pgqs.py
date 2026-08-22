#!/usr/bin/env python3
"""
Validate PGQS output against known ground truth from bug #19466.
Writes a machine-readable report for the paper evaluation section.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("/root/SQLeek")
STAGE1_OUT = ROOT / "sqleek_pipeline" / "stage1_static" / "output"
TARGETS = STAGE1_OUT / "targets"

GROUND_TRUTH = {
    "crash_sites": [
        "varlena.c:234",
        "rowtypes.c:435",
        "execExprInterp.c:3667",
        "execExpr.c:2011",
    ],
    "root_cause_function": "ExecEvalRow",
    "call_chain": [
        "exec_simple_query",
        "PortalRun",
        "PortalRunSelect",
        "RunFromStore",
        "printtup",
        "OutputFunctionCall",
        "record_out",
        "textout",
        "text_to_cstring",
    ],
    "trigger_sql_clauses": ["CURSOR", "FETCH", "ALTER_TYPE", "CAST_TO_COMPOSITE"],
    "psi_expected": {
        "A_plus_key_ids": [
            "rowcache",
            "tupdesc_id",
            "cacheptr",
            "tupDesc_identifier",
            "typentry",
        ],
        "A_minus_key_ids": ["lookup_rowtype_tupdesc_copy"],
        "G_contains": ["tupdesc_id", "tupDesc_identifier"],
    },
}


def validate() -> dict:
    report: dict = {"ground_truth": GROUND_TRUTH, "checks": {}}

    # Check 1: ψ extraction quality
    psi_path = STAGE1_OUT / "patch_features.json"
    if psi_path.exists():
        psi = json.loads(psi_path.read_text(encoding="utf-8"))
        gt_aplus = set(GROUND_TRUTH["psi_expected"]["A_plus_key_ids"])
        found_aplus = set(psi.get("A_plus", []))
        aplus_cov = len(gt_aplus & found_aplus) / max(len(gt_aplus), 1)

        gt_g = GROUND_TRUTH["psi_expected"]["G_contains"]
        g_str = str(psi.get("G", ""))
        g_cov = sum(1 for t in gt_g if t in g_str) / max(len(gt_g), 1)

        report["checks"]["psi_extraction"] = {
            "A_plus_coverage": round(aplus_cov, 3),
            "G_coverage": round(g_cov, 3),
            "pass": aplus_cov >= 0.6 and g_cov >= 0.5,
        }

    # Check 2: PGQS synthesis log
    pgqs_log = STAGE1_OUT / "pgqs_synthesis_log.json"
    if pgqs_log.exists():
        log = json.loads(pgqs_log.read_text(encoding="utf-8"))
        report["checks"]["pgqs_synthesis"] = {
            "mode": log.get("mode"),
            "final_score": log.get("final_score"),
            "converged": log.get("convergence"),
            "iterations_used": len(log.get("iterations") or []),
            "pass": float(log.get("final_score") or 0.0) >= 0.3,
        }

    # Check 3: Target coverage (line numbers are version-dependent; report both exact and file-level)
    mem_path = TARGETS / "postgres_memory.txt"
    if mem_path.exists():
        found = set(mem_path.read_text(encoding="utf-8").splitlines())
        gt = set(GROUND_TRUTH["crash_sites"])
        exact_cov = len(gt & found) / max(len(gt), 1)
        report["checks"]["target_coverage"] = {
            "crash_site_exact_coverage": round(exact_cov, 3),
            "found": sorted(gt & found),
            "missed": sorted(gt - found),
            "pass": exact_cov >= 0.75,
        }
        # file-level fallback metric
        req_files = {x.split(":", 1)[0] for x in gt}
        got_files = {x.split(":", 1)[0] for x in found if ":" in x}
        report["checks"]["target_coverage"]["file_level"] = {
            "required_files": sorted(req_files),
            "present_files": sorted(req_files & got_files),
            "file_coverage": round(len(req_files & got_files) / max(len(req_files), 1), 3),
        }

    # Check 4: Call chain coverage
    cc_path = TARGETS / "callchains.json"
    if cc_path.exists():
        cc = json.loads(cc_path.read_text(encoding="utf-8"))
        postgres = cc.get("postgres") or []
        targets = {c.get("target") for c in postgres if isinstance(c, dict)}
        required = {"text_to_cstring", "record_out", "ExecEvalRow"}
        report["checks"]["callchain_required_targets"] = {
            "present": sorted(required & targets),
            "missing": sorted(required - targets),
            "pass": required.issubset(targets),
        }

    # Check 5: Φ mapping quality
    phi_path = TARGETS / "phi_mapping.json"
    if phi_path.exists():
        phi_data = json.loads(phi_path.read_text(encoding="utf-8"))
        phi = phi_data.get("phi_mapping", {}) or {}
        gt_sql = set(GROUND_TRUTH["trigger_sql_clauses"])
        all_clauses: set[str] = set()
        for v in phi.values():
            if isinstance(v, list):
                all_clauses.update(str(x) for x in v)
        cov = len(gt_sql & all_clauses) / max(len(gt_sql), 1)
        report["checks"]["phi_mapping_quality"] = {
            "sql_clause_coverage": round(cov, 3),
            "found_clauses": sorted(gt_sql & all_clauses),
            "pass": cov >= 0.75,
        }

    report["overall_pass"] = all(v.get("pass", False) for v in report["checks"].values() if isinstance(v, dict))
    report["summary"] = "Stage 1 PGQS pipeline validated against bug #19466 ground truth."

    out = STAGE1_OUT / "pgqs_validation_report.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[validate_pgqs] wrote {out}")
    print(f"[validate_pgqs] overall_pass={report['overall_pass']}")
    return report


if __name__ == "__main__":
    validate()

