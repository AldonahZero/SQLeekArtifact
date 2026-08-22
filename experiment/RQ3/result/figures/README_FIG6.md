# Figure 6 Bug Discovery Curves

This directory contains the plotting workflow for Figure 6: cumulative unique
bugs discovered during 24-hour campaigns. The plotting script consumes an
event-level CSV produced from experiment logs and validation records.

The event-level schema is:

```csv
tool,dbms,run,bug_id,first_seen_hours
SQLeek,PostgreSQL,r1,PG-19466,0.008889
```

Each row records the first time, in hours from campaign start, at which one bug
was discovered in one independent run. Times must be in `[0, 24]`. Runs are
`r1` through `r5`.

## Statistical Definition

Figure 6 reports a per-time mean curve with a minimum--maximum shaded band, not
a five-run bug union.

The plotting script uses step curves and computes cumulative counts only at
`1, 5, 10, 15, 20, 24` hours, keeping the plotted data points sparse and close
to the DynSQL-style paper figure.

For readability, non-SQLeek baseline mean curves use tiny display-only vertical
offsets when rendered so overlapping lines remain distinguishable. These offsets
do not change the event data, checkpoint statistics, min--max bands, y-axis
ranges, or Table 2-style accounting.

The x-axis is labeled `Time (h)` and uses ticks `0h, 5h, 10h, 15h, 20h, 24h`.

For each checkpoint `t` and each observed `(tool, DBMS, run)`:

1. Deduplicate rows by `bug_id` within that run.
2. Count unique bugs with `first_seen_hours <= t`.
3. Collect the five cumulative counts from `r1` through `r5`.
4. Plot their mean.
5. Shade the minimum-to-maximum range across runs.
6. Also write sample standard deviation computed with `ddof=1` to the stats
   CSV for inspection.

The mean of five per-run cumulative counts differs from Table 2 style five-run
union accounting. A 24-hour endpoint in this figure must not be interpreted as
the union of all bugs found across the five campaigns.

## Supported and N/A Configurations

The input contains `r1` through `r5` events for each supported configuration in
the Figure 6 matrix. Unsupported configurations are omitted from their
subplots instead of being drawn as zero-valued curves.

Current N/A pairs:

- `SQLaser` on `MariaDB`
- `SQLaser` on `MonetDB`
- `SQUIRREL` on `MonetDB`

## Input preparation

Prepare a CSV with the event-level schema above and pass it through `--input`.
The plotting logic, tool order, colors, dash patterns, and layout are fixed by
the script.

For MySQL, the SQLeek rows were extracted from:

- `/root/dfuzz-griffin/griffin_output/griffin_mysql_sqleek_rnd1`
- `/root/dfuzz-griffin/griffin_output/griffin_mysql_sqleek_rnd2`
- `/root/dfuzz-griffin/griffin_output/griffin_mysql_sqleek_rnd3`
- `/root/dfuzz-griffin/griffin_output/griffin_mysql_sqleek_rnd4`
- `/root/dfuzz-griffin/griffin_output/griffin_mysql_sqleek_rnd5`

Only crashes matching existing developer-feedback validation signatures under
`/root/SQLeek/sqleek_pipeline/stage4_triage/output/report/validation/mysql`
are counted. The derived files are:

- `data/fig6_sqleek_mysql_real_bug_events_r1_r5_feedback_filtered.csv`
- `data/fig6_sqleek_mysql_real_checkpoint_stats_r1_r5_feedback_filtered.csv`
- `data/fig6_sqleek_mysql_feedback_match_review_r1_r5.csv`

For MonetDB, the SQLeek rows were extracted from:

- `/root/dfuzz-griffin/griffin_output/griffin_monetdb_sqleek_rnd1`
- `/root/dfuzz-griffin/griffin_output/griffin_monetdb_sqleek_rnd2`
- `/root/dfuzz-griffin/griffin_output/griffin_monetdb_sqleek_rnd3`
- `/root/dfuzz-griffin/griffin_output/griffin_monetdb_sqleek_rnd4`
- `/root/dfuzz-griffin/griffin_output/griffin_monetdb_sqleek_rnd5`

Only crashes matching existing validation signatures under
`/root/SQLeek/sqleek_pipeline/stage4_triage/output/report/validation/monetdb`
are counted. The derived files are:

- `data/fig6_sqleek_monetdb_real_bug_events_r1_r5_feedback_filtered.csv`
- `data/fig6_sqleek_monetdb_real_checkpoint_stats_r1_r5_feedback_filtered.csv`
- `data/fig6_sqleek_monetdb_feedback_match_review_r1_r5.csv`

The script validates:

- Required columns: `tool,dbms,run,bug_id,first_seen_hours`
- Time range `0-24`
- Runs limited to `r1-r5`
- Duplicate `(tool, dbms, run, bug_id)` rows
- Unknown tools or DBMS names
- Unsupported pairs appearing in the input
- Missing `r1-r5` entries for observed supported pairs unless explicitly
  allowed or represented with empty `bug_id` zero-run rows

Any validation failure exits with an explicit error.

## Regenerating

Run from this directory:

```bash
python3 plot_fig6_bug_discovery.py \
  --input data/fig6_bug_events.csv \
  --output-prefix Figure6_Bug_Discovery_Over_Time \
  --allow-partial-runs \
  --zero-missing-runs-for SQLeek:PostgreSQL \
  --stats-output data/fig6_checkpoint_stats.csv
```

This generates:

- `Figure6_Bug_Discovery_Over_Time.pdf`
- `Figure6_Bug_Discovery_Over_Time.png`
- `Figure6_Bug_Discovery_Over_Time.svg`

PDF and SVG are vector outputs. PNG is written at 300 dpi.
