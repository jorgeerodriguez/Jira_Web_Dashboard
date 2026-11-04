"""Build the Executive Report (HTML, and PDF when Google Chrome is installed) for one month.

Usage:
  .venv/bin/python docs/executive_report/make_report.py                  # last full month, fetched live from Jira
  .venv/bin/python docs/executive_report/make_report.py --month 2026-10
  .venv/bin/python docs/executive_report/make_report.py --issues issues.pkl [--history history.pkl]

Without --issues the data is fetched with the same code and Jira settings as the app's
"Fetch All Jira Tickets" button (read-only). issues.pkl and history.pkl are what
data/build_dataframe_new.build_issues_dataframe and data/fetch_change_history.fetch_change_history return.

Output goes to docs/executive_report/output/ (git-ignored: the reports name tickets and business leads and the
GitHub repo is public). See reports/executive_report.py for what the report contains.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from reports import executive_report as er  # noqa: E402

OUT = Path(__file__).resolve().parent / "output"


def _fetch() -> tuple[pd.DataFrame, pd.DataFrame, datetime]:
    from config.validate_and_connect_to_jira import validate_jira_connection
    from data.build_dataframe_new import build_issues_dataframe
    from data.fetch_change_history import fetch_change_history

    code, message, jira = validate_jira_connection()
    if code != 0 or jira is None:
        sys.exit(f"Could not connect to Jira: {message}")
    print("Fetching tickets…", flush=True)
    df = build_issues_dataframe(jira, projects=("DEVOPS", "CAR"))
    print(f"  {len(df):,} tickets. Fetching change history…", flush=True)
    history = fetch_change_history(jira, df)
    print(f"  {len(history):,} changes.", flush=True)
    return df, history, datetime.now()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--month", help="YYYY-MM (default: the last full month)")
    parser.add_argument("--issues", type=Path, help="pickled tickets instead of fetching from Jira")
    parser.add_argument("--history", type=Path, help="pickled change history (with --issues)")
    parser.add_argument("--out", type=Path, default=OUT, help=f"output folder (default: {OUT.relative_to(ROOT)})")
    parser.add_argument("--no-pdf", action="store_true", help="HTML only")
    args = parser.parse_args()

    if args.issues:
        df = pd.read_pickle(args.issues)
        history = pd.read_pickle(args.history) if args.history else None
        fetched_at = datetime.fromtimestamp(args.issues.stat().st_mtime)
    else:
        df, history, fetched_at = _fetch()

    report = er.build_executive_report(df, history, month=args.month, fetched_at=fetched_at)
    if report.get("error_message"):
        sys.exit(report["error_message"])
    html = er.render_html(report)
    args.out.mkdir(parents=True, exist_ok=True)
    html_path = args.out / er.filename(report)
    html_path.write_text(html, encoding="utf-8")
    print(f"HTML: {html_path}")
    if not args.no_pdf:
        pdf = er.html_to_pdf(html)
        if pdf is None:
            print("PDF: skipped (Google Chrome not found, or printing failed).")
        else:
            pdf_path = args.out / er.filename(report, "pdf")
            pdf_path.write_bytes(pdf)
            print(f"PDF:  {pdf_path}")


if __name__ == "__main__":
    main()
