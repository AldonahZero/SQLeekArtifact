#!/usr/bin/env python3
"""Run group statistics and logistic regressions for Observation 1."""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np

from common import (
    bh_adjust,
    canonical_dbms,
    data_dir,
    exp_dir,
    fisher_or_chi2,
    int_or_zero,
    odds_ratio_ci,
    quantile,
    read_csv,
    results_dir,
    safe_div,
    truthy,
    write_csv,
)


GROUPS = ["None", "H only", "C only", "S only", "H+C", "H+S", "C+S", "H+C+S"]


def fval(row: dict[str, str], key: str, default: float = 0.0) -> float:
    try:
        val = row.get(key, "")
        if val == "":
            return default
        return float(val)
    except Exception:
        return default


def signal(row: dict[str, str], key: str) -> bool:
    return truthy(row.get(key, ""))


def group_label(row: dict[str, str]) -> str:
    h = signal(row, "historical_repair_signal")
    c = signal(row, "coverage_gap_signal")
    s = signal(row, "sql_reachable")
    if h and c and s:
        return "H+C+S"
    if h and c:
        return "H+C"
    if h and s:
        return "H+S"
    if c and s:
        return "C+S"
    if h:
        return "H only"
    if c:
        return "C only"
    if s:
        return "S only"
    return "None"


def load_dataset(dbms: str) -> list[dict[str, str]]:
    if dbms == "combined":
        rows = []
        for name in ["mysql", "postgresql"]:
            rows.extend(read_csv(data_dir(name) / "risk_region_dataset.csv"))
        return rows
    return read_csv(data_dir(dbms) / "risk_region_dataset.csv")


def main_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    selected = [r for r in rows if truthy(r.get("primary_window", ""))]
    return selected or rows


def group_statistics(rows: list[dict[str, str]]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    rows = main_rows(rows)
    for row in rows:
        row["_group"] = group_label(row)
    none_rows = [r for r in rows if r["_group"] == "None"]
    none_count = len(none_rows)
    none_pos = sum(1 for r in none_rows if signal(r, "future_bug_fixed"))
    none_rate = safe_div(none_pos, none_count)
    out = []
    for group in GROUPS:
        group_rows = [r for r in rows if r["_group"] == group]
        n = len(group_rows)
        pos = sum(1 for r in group_rows if signal(r, "future_bug_fixed"))
        counts = [int_or_zero(r.get("future_unique_bug_count")) for r in group_rows]
        rate = safe_div(pos, n)
        a, b, c, d = pos, n - pos, none_pos, none_count - none_pos
        test, p = fisher_or_chi2(a, b, c, d) if group != "None" and n and none_count else ("reference", math.nan)
        orv, lo, hi = odds_ratio_ci(a, b, c, d) if n and none_count else (math.nan, math.nan, math.nan)
        out.append(
            {
                "group": group,
                "region_count": n,
                "future_bug_region_count": pos,
                "future_bug_rate": rate,
                "future_unique_bug_count": sum(counts),
                "mean_future_bug_count": float(np.mean(counts)) if counts else 0,
                "median_future_bug_count": float(np.median(counts)) if counts else 0,
                "relative_risk": safe_div(rate, none_rate) if group != "None" else 1.0,
                "risk_difference": rate - none_rate if group != "None" else 0.0,
                "odds_ratio": orv if group != "None" else 1.0,
                "ci95_low": lo if group != "None" else "",
                "ci95_high": hi if group != "None" else "",
                "test": test,
                "p_value": p,
            }
        )
    pvals = [float(r["p_value"]) if r["p_value"] == r["p_value"] else math.nan for r in out]
    adjusted = bh_adjust(pvals)
    for row, adj in zip(out, adjusted):
        row["p_value_bh"] = adj

    comparisons = []
    base = {g: [r for r in rows if r["_group"] == g] for g in GROUPS}
    for left, right in [("H+C+S", "None"), ("H+C+S", "H only"), ("H+C+S", "C only"), ("H+C+S", "S only")]:
        lr = base[left]
        rr = base[right]
        a = sum(1 for r in lr if signal(r, "future_bug_fixed"))
        b = len(lr) - a
        c = sum(1 for r in rr if signal(r, "future_bug_fixed"))
        d = len(rr) - c
        test, p = fisher_or_chi2(a, b, c, d) if lr and rr else ("insufficient", math.nan)
        orv, lo, hi = odds_ratio_ci(a, b, c, d) if lr and rr else (math.nan, math.nan, math.nan)
        comparisons.append(
            {
                "comparison": f"{left} vs {right}",
                "left_region_count": len(lr),
                "left_future_bug_region_count": a,
                "left_future_bug_rate": safe_div(a, len(lr)),
                "right_region_count": len(rr),
                "right_future_bug_region_count": c,
                "right_future_bug_rate": safe_div(c, len(rr)),
                "relative_risk": safe_div(safe_div(a, len(lr)), safe_div(c, len(rr))),
                "risk_difference": safe_div(a, len(lr)) - safe_div(c, len(rr)),
                "odds_ratio": orv,
                "ci95_low": lo,
                "ci95_high": hi,
                "test": test,
                "p_value": p,
            }
        )
    adj_comp = bh_adjust([float(r["p_value"]) if r["p_value"] == r["p_value"] else math.nan for r in comparisons])
    for row, adj in zip(comparisons, adj_comp):
        row["p_value_bh"] = adj
    return out, comparisons


def standardize(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mu = x.mean(axis=0)
    sd = x.std(axis=0)
    sd[sd == 0] = 1
    return (x - mu) / sd, mu, sd


def normal_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2.0))


def logistic_fit(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, str]:
    beta = np.zeros(x.shape[1])
    status = "converged"
    for _ in range(100):
        eta = np.clip(x @ beta, -35, 35)
        p = 1 / (1 + np.exp(-eta))
        w = np.maximum(p * (1 - p), 1e-6)
        z = eta + (y - p) / w
        xtw = x.T * w
        h = xtw @ x + np.eye(x.shape[1]) * 1e-6
        try:
            new_beta = np.linalg.solve(h, xtw @ z)
        except np.linalg.LinAlgError:
            new_beta = np.linalg.pinv(h) @ (xtw @ z)
            status = "used_pseudoinverse"
        if np.max(np.abs(new_beta - beta)) < 1e-6:
            beta = new_beta
            break
        beta = new_beta
    else:
        status = "max_iter"
    eta = np.clip(x @ beta, -35, 35)
    p = 1 / (1 + np.exp(-eta))
    w = np.maximum(p * (1 - p), 1e-6)
    h = (x.T * w) @ x + np.eye(x.shape[1]) * 1e-6
    cov = np.linalg.pinv(h)
    se = np.sqrt(np.maximum(np.diag(cov), 0))
    return beta, se, status


def vif_values(x: np.ndarray, names: list[str]) -> dict[str, float]:
    out = {}
    for i, name in enumerate(names):
        if name == "intercept":
            continue
        y = x[:, i]
        other = np.delete(x, i, axis=1)
        try:
            coef = np.linalg.lstsq(other, y, rcond=None)[0]
            pred = other @ coef
            ss_res = float(((y - pred) ** 2).sum())
            ss_tot = float(((y - y.mean()) ** 2).sum())
            r2 = 1 - ss_res / ss_tot if ss_tot else 0
            out[name] = 1 / max(1e-9, 1 - r2)
        except Exception:
            out[name] = math.nan
    return out


def component_dummies(rows: list[dict[str, str]], max_components: int = 8) -> list[str]:
    counts: dict[str, int] = {}
    for row in rows:
        comp = row.get("component", "")
        if comp:
            counts[comp] = counts.get(comp, 0) + 1
    return [k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:max_components]]


def build_design(rows: list[dict[str, str]], interaction: bool) -> tuple[np.ndarray, np.ndarray, list[str]]:
    y = np.array([1.0 if signal(r, "future_bug_fixed") else 0.0 for r in rows])
    components = component_dummies(rows)
    numeric_cols = [
        "region_loc",
        "log1p_code_churn_total",
        "log1p_total_commit_count",
        "unique_author_count",
        "region_age_days",
    ]
    base = []
    names = ["intercept"]
    for row in rows:
        h_count = fval(row, "historical_unique_bug_count")
        h_sig = 1.0 if signal(row, "historical_repair_signal") else 0.0
        c_sig = 1.0 if signal(row, "coverage_gap_signal") else 0.0
        s_sig = 1.0 if signal(row, "sql_reachable") else 0.0
        vals = [1.0]
        if interaction:
            vals.extend([h_sig, c_sig, s_sig, h_sig * c_sig, h_sig * s_sig, c_sig * s_sig, h_sig * c_sig * s_sig])
        else:
            vals.extend([h_count, c_sig, s_sig])
        vals.extend(
            [
                fval(row, "region_loc"),
                math.log1p(max(0.0, fval(row, "code_churn_total"))),
                math.log1p(max(0.0, fval(row, "total_commit_count"))),
                fval(row, "unique_author_count"),
                fval(row, "region_age_days") / 365.25,
            ]
        )
        dbms = row.get("dbms", "")
        if any(r.get("dbms") != rows[0].get("dbms") for r in rows):
            vals.append(1.0 if dbms == "postgresql" else 0.0)
        for comp in components:
            vals.append(1.0 if row.get("component") == comp else 0.0)
        base.append(vals)
    if interaction:
        names.extend(["historical_repair_signal", "coverage_gap_signal", "sql_reachable", "H:C", "H:S", "C:S", "H:C:S"])
    else:
        names.extend(["historical_unique_bug_count", "coverage_gap_signal", "sql_reachable"])
    names.extend(numeric_cols)
    if any(r.get("dbms") != rows[0].get("dbms") for r in rows):
        names.append("dbms_postgresql")
    names.extend([f"component:{c}" for c in components])
    x = np.array(base, dtype=float)
    if x.shape[1] > 1:
        x_nonint, _, _ = standardize(x[:, 1:])
        x = np.column_stack([x[:, 0], x_nonint])
    return x, y, names


def regression(rows: list[dict[str, str]], interaction: bool) -> list[dict[str, object]]:
    rows = main_rows(rows)
    if len(rows) < 10 or sum(1 for r in rows if signal(r, "future_bug_fixed")) < 2:
        return [
            {
                "model": "interaction" if interaction else "main_effects",
                "term": "MODEL_STATUS",
                "status": "insufficient_positive_samples",
                "sample_count": len(rows),
                "positive_count": sum(1 for r in rows if signal(r, "future_bug_fixed")),
            }
        ]
    x, y, names = build_design(rows, interaction)
    beta, se, status = logistic_fit(x, y)
    vifs = vif_values(x, names)
    out = []
    for name, coef, serr in zip(names, beta, se):
        z = coef / serr if serr else math.nan
        lo = coef - 1.96 * serr if serr == serr else math.nan
        hi = coef + 1.96 * serr if serr == serr else math.nan
        out.append(
            {
                "model": "interaction" if interaction else "main_effects",
                "term": name,
                "coefficient": coef,
                "standard_error": serr,
                "odds_ratio": math.exp(coef) if abs(coef) < 700 else math.inf,
                "ci95_low": math.exp(lo) if lo == lo and abs(lo) < 700 else "",
                "ci95_high": math.exp(hi) if hi == hi and abs(hi) < 700 else "",
                "z_value": z,
                "p_value": normal_p(z) if z == z else "",
                "sample_count": len(rows),
                "positive_count": int(y.sum()),
                "vif": vifs.get(name, ""),
                "status": status,
            }
        )
    return out


def top_risk_regions(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    rows = main_rows(rows)
    scored = []
    for row in rows:
        score = int(signal(row, "historical_repair_signal")) + int(signal(row, "coverage_gap_signal")) + int(signal(row, "sql_reachable"))
        scored.append(
            {
                "dbms": row.get("dbms", ""),
                "region_id": row.get("region_id", ""),
                "file_path": row.get("file_path", ""),
                "start_line": row.get("start_line", ""),
                "end_line": row.get("end_line", ""),
                "enclosing_function": row.get("enclosing_function", ""),
                "component": row.get("component", ""),
                "signal_count": score,
                "historical_unique_bug_count": row.get("historical_unique_bug_count", ""),
                "coverage_gap_signal": row.get("coverage_gap_signal", ""),
                "sql_reachable": row.get("sql_reachable", ""),
                "future_bug_fixed": row.get("future_bug_fixed", ""),
                "future_unique_bug_count": row.get("future_unique_bug_count", ""),
                "future_unique_bug_ids": row.get("future_unique_bug_ids", ""),
            }
        )
    scored.sort(key=lambda r: (-int(r["signal_count"]), -int_or_zero(r["historical_unique_bug_count"]), str(r["dbms"]), str(r["file_path"])))
    return scored[:200]


def output_dir(dbms: str) -> Path:
    return exp_dir() / "results" / "combined" if dbms == "combined" else results_dir(dbms)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbms", required=True, choices=["mysql", "postgresql", "postgres", "combined"])
    args = parser.parse_args()
    dbms = "combined" if args.dbms == "combined" else canonical_dbms(args.dbms)
    rows = load_dataset(dbms)
    outdir = output_dir(dbms)
    groups, comparisons = group_statistics(rows)
    write_csv(outdir / "group_statistics.csv", groups)
    write_csv(outdir / "group_comparisons.csv", comparisons)
    reg_rows = regression(rows, interaction=False) + regression(rows, interaction=True)
    write_csv(outdir / "regression_results.csv", reg_rows)
    write_csv(outdir / "top_risk_regions.csv", top_risk_regions(rows))
    print(outdir)


if __name__ == "__main__":
    main()
