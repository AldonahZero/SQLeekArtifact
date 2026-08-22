# LLVM Replay Coverage Skill

Use this skill when replaying fuzzing queues into an LLVM source-coverage DBMS
build to produce RQ2 coverage checkpoints.

Scope:
- This profile-management strategy is DBMS-independent for LLVM coverage builds
  that emit `*.profraw` via `LLVM_PROFILE_FILE` and are summarized with
  `llvm-profdata` / `llvm-cov`.
- It applies to MySQL, MariaDB, PostgreSQL, MonetDB, SQLite, and similar replay
  targets when the replay runner can execute queue inputs against an LLVM
  instrumented binary.
- It does not replace DBMS-specific replay logic: server initialization,
  socket/port/farm/datadir handling, input execution, reset policy, SQL dialect,
  source-root mapping, and target-region mapping remain DBMS-specific.

Required replay shape:
- Select queue entries deterministically by original queue time or mtime.
- Preserve a replay index with queue filename, logical elapsed time, source path,
  and execution status.
- Execute inputs once with a bounded timeout; do not retry a failed input forever.
- Recreate or restart the DBMS only through the current replay runner's isolated
  runtime paths. Never stop unrelated DBMS instances.
- Produce cumulative coverage checkpoints such as `t60`, `t180`, `t300`, `t480`,
  `t600`, `t720`, `t900`, `t1200`, and `t1440` when requested.

Profile bucketing rule:
- Never accumulate all raw `*.profraw` files until the end of a long replay.
- Write raw profiles into a per-run raw directory, for example:
  `profiles/<run_id>/raw/%p-%m.profraw`.
- Flush profiles at least on:
  - checkpoint boundary;
  - DBMS restart;
  - server crash or missing server recovery;
  - `PROFILE_BUCKET_SIZE` inputs, default around 200 to 500;
  - low-disk guard threshold.
- Merge each bucket with:
  `llvm-profdata merge --failure-mode=warn -sparse -f <raw_profiles.list> -o <bucket.profdata>`.
- Merge each bucket into one cumulative profile:
  `llvm-profdata merge --failure-mode=warn -sparse <cumulative.profdata> <bucket.profdata> -o <tmp>`.
- Replace the cumulative profile with atomic rename after merge succeeds.
- Delete raw `*.profraw` only after the bucket has been successfully merged into
  the cumulative profile.
- Keep bucket `*.profdata` and merge logs unless disk pressure requires pruning;
  never delete checkpoint `t*.profdata` files needed for coverage-over-time.

Checkpoint rule:
- A checkpoint profile is a copy of cumulative coverage at that logical time.
- Example:
  - `t60.profdata` means all inputs up to 60 minutes;
  - `t720.profdata` means all inputs up to 720 minutes;
  - `t1440.profdata` means all inputs up to 1440 minutes.
- Checkpoints must not depend on retaining raw `*.profraw` files.
- Continue writing the existing `checkpoint_meta.tsv` contract:
  `checkpoint_min, seed_count, profile_count, profdata, report_txt`.

Required metadata:
- `input_status.tsv`: one complete row per input, including timestamp, seed time,
  seed name, path, and exit code.
- `server_restarts.tsv`: restart/recovery/checkpoint/bucket-flush events.
- `profile_bucket_meta.tsv`: bucket id, reason, seed count, raw profile count,
  raw profile bytes, bucket profdata, cumulative profdata, warning count, merge log.
- `checkpoint_meta.tsv`: checkpoint rows consumed by downstream RQ2 summarizers.
- Result files must clearly mark partial or degraded results if any input was not
  replayed, if profile writes failed, or if corrupt profiles were skipped.

Coverage summarization:
- Prefer DBMS-specific summarizers that avoid huge full JSON exports.
- For MySQL, use target-source LCOV plus summary-only JSON rather than full
  `llvm-cov export -format=text` for all sources; full export can be multiple GB.
- Keep denominator contracts stable. For SQLeek MySQL RQ2, the denominators are
  target branches `4602`, target regions `1445`, and global branches `326944`.
- Do not write pilot or partial replay results into formal RQ2 summary tables or
  figures unless the run completed cleanly and the experiment owner asks for it.

Failure handling:
- Use `--failure-mode=warn` for LLVM profile merges so a small number of corrupt
  or zero-byte profiles does not invalidate all prior coverage.
- Record the warning count and preserve merge logs.
- If a bucket merge fails completely, stop the replay and keep logs and the
  current input status; do not claim a complete checkpoint.
- Add a disk guard. If free space drops below the configured threshold, flush the
  current bucket before executing more inputs.

Validation gate for a new runner:
- Run a tiny isolated replay first, for example 3 to 10 queue inputs.
- Force at least two bucket flushes using a small `PROFILE_BUCKET_SIZE`.
- Verify:
  - replay exits with code 0;
  - checkpoint `t*.profdata` is non-empty;
  - `checkpoint_meta.tsv` references the checkpoint profile;
  - `profile_bucket_meta.tsv` has bucket rows;
  - raw `*.profraw` remaining count is 0 after successful completion;
  - no replay DBMS, wrapper, or merge container remains running.
- For MySQL-like runners using `/dev/shm` datadir, run containers with an explicit
  `--shm-size` such as `8g`; the default Docker `/dev/shm` is too small.

Current reference implementation:
- SQLeek Stage3 MySQL runner:
  `/root/SQLeek/sqleek_pipeline/stage3_scheduler/scripts/replay/mysql_clean_bucketed_replay_runner_with_status.sh`
- This implementation keeps cumulative `t*.profdata` checkpoints while deleting
  raw profiles after each bucket merge.
