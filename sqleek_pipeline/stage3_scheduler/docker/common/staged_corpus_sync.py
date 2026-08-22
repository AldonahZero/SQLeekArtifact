#!/usr/bin/env python3
"""Stage a large SQL corpus into AFL sync after bootstrap dry run.

This helper is intentionally conservative. It keeps the initial AFL input set
small, waits until SQLRight/AFL has finished the initial dry run, then exposes
the remaining corpus through an AFL sync sibling directory in bounded batches.
The target fuzzer must run with a sync id, for example `-S default`, so AFL's
native `sync_fuzzers()` path executes imported inputs. Standard AFL keeps
only coverage-interesting cases in the real queue; SQLeek can additionally
retain directed staged inputs via SQLRIGHT_SYNC_KEEP_NONCOV=1 so the scheduler
can assign energy to them later.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import signal
import time
from pathlib import Path

RUNNING = True


def stop(_signum, _frame) -> None:
    global RUNNING
    RUNNING = False


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)


def log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
    with path.open("a", encoding="utf-8") as fp:
        fp.write(line)
    print(f"[sqleek-staged-corpus] {message}", flush=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def nonempty_files(path: Path) -> list[Path]:
    if not path.is_dir():
        return []
    return sorted(
        (p for p in path.iterdir() if p.is_file() and p.stat().st_size > 0),
        key=lambda p: p.name.lower(),
    )


def count_queue_ids(queue_dir: Path) -> int:
    if not queue_dir.is_dir():
        return 0
    return sum(1 for p in queue_dir.glob("id:*") if p.is_file())


def count_lines(path: Path) -> int:
    try:
        with path.open("rb") as fp:
            return sum(1 for _ in fp)
    except OSError:
        return 0


def tail_contains(path: Path, needle: bytes, max_bytes: int = 2 * 1024 * 1024) -> bool:
    try:
        size = path.stat().st_size
        with path.open("rb") as fp:
            if size > max_bytes:
                fp.seek(size - max_bytes)
            return needle in fp.read()
    except OSError:
        return False


def dry_run_complete(args: argparse.Namespace) -> bool:
    log_dir = args.dry_run_log_dir
    if log_dir.is_dir():
        for pattern in ("sqlright_*child*.log", "sqlright.log"):
            for path in log_dir.glob(pattern):
                if tail_contains(path, b"All test cases processed"):
                    return True

    # Fallback indicators for variants whose child log is truncated or moved:
    # plot_data gets runtime rows and the energy trace is written only after
    # fuzz_one() starts. Either means the initial dry run is no longer blocking.
    if count_lines(args.plot_data) >= 2:
        return True
    if count_lines(args.energy_trace) >= 2:
        return True
    return False


def wait_for_queue(queue_dir: Path, timeout_s: int, log_file: Path) -> bool:
    deadline = time.time() + timeout_s
    while RUNNING and time.time() < deadline:
        if count_queue_ids(queue_dir) > 0:
            return True
        time.sleep(1)
    log(log_file, f"staged import skipped: AFL queue not ready within {timeout_s}s queue={queue_dir}")
    return False


def wait_for_dry_run(args: argparse.Namespace) -> bool:
    deadline = time.time() + args.dry_run_timeout
    while RUNNING and time.time() < deadline:
        if dry_run_complete(args):
            log(args.log_file, "dry run completion observed; staged import may start")
            return True
        time.sleep(args.poll_interval)
    log(
        args.log_file,
        f"staged import skipped: dry run not complete within {args.dry_run_timeout}s",
    )
    return False


def write_queue_mtime_snapshot(queue_dir: Path, out_file: Path) -> None:
    if out_file.exists() or not queue_dir.is_dir():
        return
    tmp = out_file.with_suffix(out_file.suffix + f".tmp.{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as fp:
        fp.write("name\tmtime_epoch\tmtime_iso\tsize\n")
        rows = []
        for p in queue_dir.iterdir():
            if not p.is_file():
                continue
            st = p.stat()
            rows.append(
                (
                    p.name,
                    st.st_mtime,
                    time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(st.st_mtime)),
                    st.st_size,
                )
            )
        for name, epoch, iso, size in sorted(rows):
            fp.write(f"{name}\t{epoch:.9f}\t{iso}\t{size}\n")
    os.replace(tmp, out_file)


def atomic_copy(src: Path, dst: Path) -> None:
    tmp = dst.with_name(f".{dst.name}.tmp.{os.getpid()}")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def remaining_corpus(corpus_dir: Path, bootstrap_dir: Path) -> list[tuple[Path, str]]:
    bootstrap_shas = {sha256_file(p) for p in nonempty_files(bootstrap_dir)}
    remaining: list[tuple[Path, str]] = []
    for path in nonempty_files(corpus_dir):
        digest = sha256_file(path)
        if digest in bootstrap_shas:
            continue
        remaining.append((path, digest))
    return remaining


def import_batches(args: argparse.Namespace) -> int:
    corpus_files = nonempty_files(args.corpus_dir)
    bootstrap_files = nonempty_files(args.bootstrap_dir)
    remaining = remaining_corpus(args.corpus_dir, args.bootstrap_dir)
    log(
        args.log_file,
        "staged importer initialized "
        f"corpus_total={len(corpus_files)} bootstrap={len(bootstrap_files)} "
        f"remaining={len(remaining)} queue={args.queue_dir} sync_dir={args.sync_dir} "
        f"sync_id={args.sync_id} batch_size={args.batch_size} interval={args.batch_interval}s",
    )
    if not remaining:
        return 0
    if not wait_for_queue(args.queue_dir, args.wait_timeout, args.log_file):
        return 0
    if args.wait_for_dry_run and not wait_for_dry_run(args):
        return 0

    write_queue_mtime_snapshot(args.queue_dir, args.queue_mtime_file)

    sync_queue = args.sync_dir / args.sync_id / "queue"
    sync_queue.mkdir(parents=True, exist_ok=True)
    manifest_tmp = args.import_manifest.with_suffix(args.import_manifest.suffix + f".tmp.{os.getpid()}")
    imported = 0
    batch_no = 0
    batch_size = max(1, args.batch_size)
    with manifest_tmp.open("w", encoding="utf-8") as fp:
        fp.write("sync_queue_name\tsource_name\tsha256\tsize\tbatch\timport_time\n")
        for offset in range(0, len(remaining), batch_size):
            if not RUNNING:
                break
            batch = remaining[offset : offset + batch_size]
            for src, digest in batch:
                queue_name = f"id:{imported:06d},sqleek_stage:{batch_no:04d},src:{digest[:12]}.sql"
                dst = sync_queue / queue_name
                if dst.exists():
                    imported += 1
                    continue
                atomic_copy(src, dst)
                fp.write(
                    f"{queue_name}\t{src.name}\t{digest}\t{src.stat().st_size}\t{batch_no}\t{int(time.time())}\n"
                )
                imported += 1
            fp.flush()
            log(
                args.log_file,
                f"imported staged batch={batch_no} batch_count={len(batch)} total_imported={imported} sync_queue={sync_queue}",
            )
            batch_no += 1
            if imported < len(remaining):
                deadline = time.time() + args.batch_interval
                while RUNNING and time.time() < deadline:
                    time.sleep(min(1, deadline - time.time()))
    os.replace(manifest_tmp, args.import_manifest)
    log(args.log_file, f"staged import complete imported={imported} sync_queue={sync_queue}")
    return imported


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage a large corpus into AFL sync in bounded batches")
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-dir", type=Path, required=True)
    parser.add_argument("--queue-dir", type=Path, required=True, help="real AFL queue, e.g. .../default/queue")
    parser.add_argument("--sync-dir", type=Path, required=True, help="AFL sync parent, e.g. .../postgres_memory")
    parser.add_argument("--sync-id", default="sqleek_staged")
    parser.add_argument("--log-file", type=Path, required=True)
    parser.add_argument("--import-manifest", type=Path, required=True)
    parser.add_argument("--queue-mtime-file", type=Path, required=True)
    parser.add_argument("--dry-run-log-dir", type=Path, required=True)
    parser.add_argument("--plot-data", type=Path, required=True)
    parser.add_argument("--energy-trace", type=Path, required=True)
    parser.add_argument("--wait-timeout", type=int, default=300)
    parser.add_argument("--dry-run-timeout", type=int, default=3600)
    parser.add_argument("--poll-interval", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--batch-interval", type=int, default=60)
    parser.add_argument("--wait-for-dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    import_batches(parse_args())


if __name__ == "__main__":
    main()
