#!/usr/bin/env python3
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path("/root/SQLeek")))
REPORT_DIR = Path("/root/SQLeek/sqleek_pipeline/stage4_triage/output")


def log(message: str) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with (REPORT_DIR / "build.log").open("a", encoding="utf-8") as fp:
        fp.write(f"[report_gen] {message}\n")
    print(f"[report_gen] {message}")


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def render_html(report: dict) -> str:
    bugs = report.get("bugs", [])
    rows = []
    for bug in bugs:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(bug.get('id', '')))}</td>"
            f"<td>{html.escape(str(bug.get('dbms', '')))}</td>"
            f"<td>{html.escape(str(bug.get('bug_type', '')))}</td>"
            f"<td>{html.escape(str(bug.get('duplicates', 1)))}</td>"
            f"<td><code>{html.escape(str(bug.get('crash_file') or bug.get('seed') or ''))}</code></td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>SQLeek Crash Report</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 0.5rem; text-align: left; }}
    th {{ background: #f4f4f4; }}
    code {{ word-break: break-all; }}
  </style>
</head>
<body>
  <h1>SQLeek Crash Report</h1>
  <p>Total crashes: {html.escape(str(report.get('total_crashes', 0)))}</p>
  <p>Unique bugs: {html.escape(str(report.get('unique_crashes', len(bugs))))}</p>
  <table>
    <thead>
      <tr><th>ID</th><th>DBMS</th><th>Type</th><th>Duplicates</th><th>Input</th></tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</body>
</html>
"""


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    crash_report = load_json(REPORT_DIR / "crash_report.json", {
        "total_crashes": 0,
        "unique_crashes": 0,
        "bugs": [],
    })
    logic_report = load_json(REPORT_DIR / "logic_report.json", {"logic_bugs": []})

    bugs = crash_report.get("bugs", []) + logic_report.get("logic_bugs", [])
    merged = {
        "total_crashes": crash_report.get("total_crashes", 0),
        "unique_crashes": len(bugs),
        "bugs": bugs,
        "summary": {
            "memory": sum(1 for bug in bugs if bug.get("bug_type") == "memory"),
            "logic": sum(1 for bug in bugs if bug.get("bug_type") == "logic"),
            "unknown": sum(1 for bug in bugs if bug.get("bug_type") == "unknown"),
        },
    }

    json_path = REPORT_DIR / "crash_report.json"
    html_path = REPORT_DIR / "crash_report.html"
    json_path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    html_path.write_text(render_html(merged), encoding="utf-8")

    log(f"wrote {json_path}")
    log(f"wrote {html_path}")


if __name__ == "__main__":
    main()
