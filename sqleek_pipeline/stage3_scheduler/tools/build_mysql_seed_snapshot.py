#!/usr/bin/env python3
import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

STAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STAGE_DIR))

from seed_scheduler import (  # noqa: E402
    classify_mysql_seed_bytes,
    iter_seed_candidates,
    sanitize_seed_component,
)


SOURCE_GROUPS = [
    (
        "official",
        Path("/root/dfuzz-griffin/docker/metadata_collector/input-set/officials_to_griffin_compatible/official_mysql"),
    ),
    (
        "select_only",
        Path("/root/dfuzz-griffin/docker/metadata_collector/input-set/select_only/mysql"),
    ),
    (
        "sqleek_memory",
        Path("/root/SQLeek/sqleek_pipeline/stage2_setup/output/seeds/mysql/memory"),
    ),
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def snapshot_name(source_group: str, relative_path: str, digest: str) -> str:
    rel = Path(relative_path)
    rel_no_ext = rel.with_suffix("")
    safe_rel = sanitize_seed_component(rel_no_ext.as_posix())
    return f"{source_group}__{safe_rel}__{digest[:12]}.sql"


def copy_unique(data_path: Path, target_dir: Path, name: str) -> str:
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    if not target.exists():
        shutil.copy2(data_path, target)
        return target.name

    source_digest = sha256_file(data_path)
    if sha256_file(target) == source_digest:
        return target.name

    stem = target.stem
    suffix = target.suffix or ".sql"
    idx = 1
    while True:
        candidate = target_dir / f"{stem}_{idx}{suffix}"
        if not candidate.exists():
            shutil.copy2(data_path, candidate)
            return candidate.name
        idx += 1


def empty_source_stats(source_path: Path) -> dict:
    return {
        "source_path": str(source_path),
        "scanned": 0,
        "non_empty": 0,
        "ignored_empty": 0,
        "bytes": 0,
        "duplicates": 0,
        "kept": 0,
        "deferred": 0,
        "rejected": 0,
        "corpus_files": 0,
        "extensions": {".sql": 0, ".test": 0},
        "mysqltest_deferred": 0,
    }


def build_snapshot(args: argparse.Namespace) -> tuple[Path, dict]:
    snapshot_id = args.snapshot_id or "mysql_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    snapshot_dir = args.output_root / snapshot_id
    if snapshot_dir.exists():
        raise SystemExit(f"snapshot already exists: {snapshot_dir}")

    corpus_dir = snapshot_dir / "corpus"
    deferred_dir = snapshot_dir / "deferred"
    rejected_dir = snapshot_dir / "rejected"
    for path in (corpus_dir, deferred_dir, rejected_dir):
        path.mkdir(parents=True, exist_ok=False)

    per_source = {group: empty_source_stats(source_path) for group, source_path in SOURCE_GROUPS}
    first_by_sha: dict[str, dict] = {}
    records: list[dict] = []
    total_bytes = 0

    for source_group, source_path in SOURCE_GROUPS:
        if not source_path.is_dir():
            raise SystemExit(f"missing source dir for {source_group}: {source_path}")
        stats = per_source[source_group]
        for path in iter_seed_candidates(source_path):
            rel = path.relative_to(source_path).as_posix()
            ext = path.suffix.lower()
            try:
                data = path.read_bytes()
            except OSError as exc:
                stats["rejected"] += 1
                records.append({
                    "source_group": source_group,
                    "source_path": str(path),
                    "relative_path": rel,
                    "original_extension": ext,
                    "size": 0,
                    "sha256": "",
                    "snapshot_name": "",
                    "duplicate_of": "",
                    "prefilter_status": "rejected",
                    "rejection_reason": f"read_error:{exc}",
                })
                continue

            size = len(data)
            if size == 0:
                stats["ignored_empty"] += 1
                continue

            digest = hashlib.sha256(data).hexdigest()
            stats["scanned"] += 1
            stats["non_empty"] += 1
            stats["extensions"][ext] = stats["extensions"].get(ext, 0) + 1
            stats["bytes"] += size
            total_bytes += size

            status, reason = classify_mysql_seed_bytes(data, ext)
            existing = first_by_sha.get(digest)
            if existing is not None:
                stats["duplicates"] += 1
                record = {
                    "source_group": source_group,
                    "source_path": str(path),
                    "relative_path": rel,
                    "original_extension": ext,
                    "size": size,
                    "sha256": digest,
                    "snapshot_name": existing["snapshot_name"],
                    "duplicate_of": existing["snapshot_name"],
                    "prefilter_status": "duplicate",
                    "rejection_reason": "exact_duplicate",
                }
                records.append(record)
                continue

            name = snapshot_name(source_group, rel, digest)
            if status == "kept":
                copied_name = copy_unique(path, corpus_dir, name)
                stats["kept"] += 1
                stats["corpus_files"] += 1
            elif status == "deferred":
                copied_name = copy_unique(path, deferred_dir, name)
                stats["deferred"] += 1
                if reason.startswith("mysqltest_directive:"):
                    stats["mysqltest_deferred"] += 1
            else:
                copied_name = copy_unique(path, rejected_dir, name)
                stats["rejected"] += 1

            record = {
                "source_group": source_group,
                "source_path": str(path),
                "relative_path": rel,
                "original_extension": ext,
                "size": size,
                "sha256": digest,
                "snapshot_name": copied_name,
                "duplicate_of": "",
                "prefilter_status": status,
                "rejection_reason": reason,
            }
            first_by_sha[digest] = record
            records.append(record)

    manifest_path = snapshot_dir / "manifest.jsonl"
    with manifest_path.open("w", encoding="utf-8") as fp:
        for record in records:
            fp.write(json.dumps(record, sort_keys=True) + "\n")

    corpus_files = sorted(p for p in corpus_dir.iterdir() if p.is_file())
    deferred_files = sorted(p for p in deferred_dir.iterdir() if p.is_file())
    rejected_files = sorted(p for p in rejected_dir.iterdir() if p.is_file())
    extension_distribution: dict[str, int] = {}
    for stats in per_source.values():
        for ext, count in stats["extensions"].items():
            extension_distribution[ext] = extension_distribution.get(ext, 0) + int(count)

    non_empty_total = sum(stats["non_empty"] for stats in per_source.values())
    summary = {
        "snapshot_id": snapshot_id,
        "snapshot_path": str(snapshot_dir),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": per_source,
        "expected_non_empty": args.expected_non_empty,
        "expected_non_empty_match": non_empty_total == args.expected_non_empty,
        "scanned_total": sum(stats["scanned"] for stats in per_source.values()),
        "non_empty_total": non_empty_total,
        "source_bytes_total": total_bytes,
        "unique_sha_total": len(first_by_sha),
        "duplicates_total": sum(stats["duplicates"] for stats in per_source.values()),
        "kept_total": sum(stats["kept"] for stats in per_source.values()),
        "deferred_total": sum(stats["deferred"] for stats in per_source.values()),
        "rejected_total": sum(stats["rejected"] for stats in per_source.values()),
        "mysqltest_deferred_total": sum(stats["mysqltest_deferred"] for stats in per_source.values()),
        "final_corpus_files": len(corpus_files),
        "final_corpus_bytes": sum(p.stat().st_size for p in corpus_files),
        "deferred_files": len(deferred_files),
        "rejected_files": len(rejected_files),
        "extension_distribution": dict(sorted(extension_distribution.items())),
        "manifest_sha256": sha256_file(manifest_path),
    }
    summary_path = snapshot_dir / "summary.json"
    write_json(summary_path, summary)
    summary["summary_sha256"] = sha256_file(summary_path)
    if args.report:
        write_report(args.report, summary)
    return snapshot_dir, summary


def write_report(path: Path, summary: dict) -> None:
    lines = [
        "# MySQL Seed Corpus Snapshot",
        "",
        f"- Snapshot: `{summary['snapshot_id']}`",
        f"- Snapshot path: `{summary['snapshot_path']}`",
        f"- Manifest SHA-256: `{summary['manifest_sha256']}`",
        f"- Summary SHA-256: `{summary['summary_sha256']}`",
        f"- Expected non-empty files: `{summary['expected_non_empty']}`",
        f"- Observed non-empty files: `{summary['non_empty_total']}`",
    ]
    if summary["expected_non_empty_match"]:
        lines.append("- Expected-count check: matched")
    else:
        lines.append("- Expected-count check: mismatch; see source table below for the observed count.")
    lines.extend([
        f"- Final corpus files: `{summary['final_corpus_files']}`",
        f"- Final corpus bytes: `{summary['final_corpus_bytes']}`",
        f"- Exact duplicates: `{summary['duplicates_total']}`",
        f"- Deferred by mysqltest directives: `{summary['mysqltest_deferred_total']}`",
        "",
        "## Source Counts",
        "",
        "| Source | Scanned non-empty | Ignored empty | Duplicates | Kept | Deferred | Rejected | Corpus files | Bytes | .sql | .test | Other SQL-like |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for source, stats in summary["sources"].items():
        other_ext = stats["scanned"] - stats["extensions"].get(".sql", 0) - stats["extensions"].get(".test", 0)
        lines.append(
            f"| `{source}` | {stats['scanned']} | {stats.get('ignored_empty', 0)} | {stats['duplicates']} | "
            f"{stats['kept']} | {stats['deferred']} | {stats['rejected']} | {stats['corpus_files']} | "
            f"{stats['bytes']} | {stats['extensions'].get('.sql', 0)} | {stats['extensions'].get('.test', 0)} | {other_ext} |"
        )
    lines.extend(["", "## Extension Distribution", ""])
    for ext, count in summary["extension_distribution"].items():
        lines.append(f"- `{ext}`: `{count}`")
    lines.extend([
        "",
        "## Handling",
        "",
        "`.sql`, `.test`, and SQL-like AFL filenames such as `.sql.0` are scanned recursively. Direct SQL content is copied byte-for-byte into `corpus/` with a normalized `.sql` filename. mysqltest control scripts such as `--source`, `--error`, `--let`, `--echo`, connection control, and file/system commands are copied into `deferred/` and are not sent silently to SQLRight. Empty files are ignored before manifest/corpus creation and counted as `ignored_empty`.",
        "",
        "Duplicates are detected by full-file SHA-256. Only the first occurrence is copied; every duplicate source is still recorded in `manifest.jsonl` with `prefilter_status=duplicate` and `duplicate_of` pointing at the retained snapshot file.",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build deterministic MySQL seed corpus snapshot for SQLeek Stage 3")
    parser.add_argument("--output-root", type=Path, default=STAGE_DIR / "runtime" / "seed_snapshots")
    parser.add_argument("--snapshot-id", default="")
    parser.add_argument("--expected-non-empty", type=int, default=8938)
    parser.add_argument("--report", type=Path, default=STAGE_DIR / "configs" / "MYSQL_SEED_CORPUS.md")
    return parser.parse_args()


def main() -> None:
    snapshot_dir, summary = build_snapshot(parse_args())
    print(f"snapshot={snapshot_dir}")
    print(f"final_corpus_files={summary['final_corpus_files']}")
    print(f"manifest_sha256={summary['manifest_sha256']}")


if __name__ == "__main__":
    main()
