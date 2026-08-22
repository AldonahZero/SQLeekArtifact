#!/usr/bin/env python3
import argparse
import hashlib
import os
import shutil
import signal
import time
from pathlib import Path

RUNNING = True


def stop(_signum, _frame):
    global RUNNING
    RUNNING = False


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)


def log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
    with path.open("a", encoding="utf-8") as fp:
        fp.write(line)
    print(f"[sqleek-corpus-importer] {message}", flush=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def count_queue_ids(queue_dir: Path) -> int:
    if not queue_dir.is_dir():
        return 0
    return sum(1 for p in queue_dir.glob("id:*") if p.is_file())


def wait_for_afl_queue(queue_dir: Path, timeout_s: int, log_file: Path) -> bool:
    deadline = time.time() + timeout_s
    while RUNNING and time.time() < deadline:
        if count_queue_ids(queue_dir) > 0:
            return True
        time.sleep(1)
    log(log_file, f"queue import skipped: no AFL id:* seed appeared in {queue_dir} within {timeout_s}s")
    return False


def atomic_copy(src: Path, dst: Path) -> None:
    tmp = dst.with_name(f".{dst.name}.tmp.{os.getpid()}")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


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
            rows.append((p.name, st.st_mtime, time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(st.st_mtime)), st.st_size))
        for name, epoch, iso, size in sorted(rows):
            fp.write(f"{name}\t{epoch:.9f}\t{iso}\t{size}\n")
    os.replace(tmp, out_file)


def import_remaining(args: argparse.Namespace) -> int:
    corpus_dir = args.corpus_dir
    bootstrap_dir = args.bootstrap_dir
    queue_dir = args.queue_dir
    log_file = args.log_file
    corpus_files = sorted(p for p in corpus_dir.iterdir() if p.is_file() and p.stat().st_size > 0)
    bootstrap_shas = {sha256_file(p) for p in bootstrap_dir.iterdir() if p.is_file() and p.stat().st_size > 0}
    remaining = []
    for path in corpus_files:
        digest = sha256_file(path)
        if digest in bootstrap_shas:
            continue
        remaining.append((path, digest))
    log(log_file, f"waiting for AFL queue corpus_total={len(corpus_files)} bootstrap={len(bootstrap_shas)} remaining={len(remaining)} queue={queue_dir}")
    if not remaining:
        return 0
    if not wait_for_afl_queue(queue_dir, args.wait_timeout, log_file):
        return 0
    write_queue_mtime_snapshot(queue_dir, args.queue_mtime_file)
    queue_dir.mkdir(parents=True, exist_ok=True)
    manifest_tmp = args.import_manifest.with_suffix(args.import_manifest.suffix + f".tmp.{os.getpid()}")
    imported = 0
    with manifest_tmp.open("w", encoding="utf-8") as fp:
        fp.write("queue_name\tsource_name\tsha256\tsize\n")
        for idx, (src, digest) in enumerate(remaining):
            if not RUNNING:
                break
            queue_name = f"id:sqleek_import_{idx:06d},src:{digest[:12]}.sql"
            dst = queue_dir / queue_name
            if dst.exists():
                continue
            atomic_copy(src, dst)
            imported += 1
            fp.write(f"{queue_name}\t{src.name}\t{digest}\t{src.stat().st_size}\n")
    os.replace(manifest_tmp, args.import_manifest)
    log(log_file, f"imported remaining corpus seeds into queue imported={imported} queue={queue_dir}")
    return imported


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import full SQLeek corpus into the current AFL queue after bootstrap")
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-dir", type=Path, required=True)
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--log-file", type=Path, required=True)
    parser.add_argument("--import-manifest", type=Path, required=True)
    parser.add_argument("--queue-mtime-file", type=Path, required=True)
    parser.add_argument("--wait-timeout", type=int, default=300)
    return parser.parse_args()


def main() -> None:
    import_remaining(parse_args())


if __name__ == "__main__":
    main()
