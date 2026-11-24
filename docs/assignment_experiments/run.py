"""Compare ways of scoring domain experience for Suggested Assignments (read-only; nothing is written to Jira).

Usage:
  .venv/bin/python docs/assignment_experiments/run.py                 # tickets fetched live from Jira
  .venv/bin/python docs/assignment_experiments/run.py --save-issues issues.pkl   # keep the fetch for re-runs
  .venv/bin/python docs/assignment_experiments/run.py --issues issues.pkl

Prints the top-3 hit rate of each variant over the last 90 and 45 days, the per-domain comparison, and the domain
families found from co-occurrence (to review by hand). "primary" is what the app does today. See
reports/assignment_experiments.py for the variants. Output is printed only: ticket keys and names stay on this machine.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

pd.set_option("display.width", 200)
pd.set_option("display.max_rows", 200)
pd.set_option("display.max_colwidth", 120)

from reports import assignment_experiments as ex  # noqa: E402
from reports import assignment_report as ar  # noqa: E402
from reports import domains  # noqa: E402
from reports import executive_summary as es  # noqa: E402
from reports import in_progress_report as ipr  # noqa: E402


def _fetch() -> pd.DataFrame:
    from config.validate_and_connect_to_jira import validate_jira_connection
    from data.build_dataframe_new import build_issues_dataframe

    code, message, jira = validate_jira_connection()
    if code != 0 or jira is None:
        sys.exit(f"Could not connect to Jira: {message}")
    print("Fetching tickets…", flush=True)
    return build_issues_dataframe(jira, projects=("DEVOPS", "CAR"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--issues", type=Path, help="pickled tickets instead of fetching from Jira")
    parser.add_argument("--save-issues", type=Path, help="save the fetched tickets here, so the next run can use --issues")
    args = parser.parse_args()
    df = pd.read_pickle(args.issues) if args.issues else _fetch()
    if args.save_issues and not args.issues:
        df.to_pickle(args.save_issues)

    today = ipr.today_local()
    facts = es._facts(df, today, ipr._calendar_holidays(today))
    t = facts[facts["sla_applies"]].copy()
    t["domains"] = [domains.tag(domains.ticket_text(row)) for _, row in t.iterrows()]
    t["domain"] = t["domains"].map(domains.primary).astype(object).where(lambda s: s.notna(), None)

    recent = t[t["outcome"].eq("Done") & (t["closed_day"] > today - pd.Timedelta(days=ar.CANDIDATE_DAYS))
               & t["assignee_name"].ne("Unassigned")]
    delivered = recent["assignee_name"].value_counts()
    people = sorted({p for p in delivered[delivered >= ar.MIN_DELIVERED].index} | set(ar.NEW_MEMBERS))
    people = [p for p in people if str(p).strip().casefold() not in ar.DEPARTED_MEMBERS]
    print(f"{len(t):,} PE tickets, {len(people)} candidates.\n")

    texts = pd.Series([ex.early_text(r) for _, r in t.iterrows()], index=t["key"].to_numpy())
    texts = texts[~texts.index.duplicated()]
    variants = ex.build_variants(t, people, texts)
    for days in (ar.ACCURACY_DAYS, 45):
        out = ex.compare(t, today, people, days=days, variants=variants)
        print(f"── Tickets completed in the last {days} days ──")
        print(out["overall"].to_string(index=False), "\n")
        base = out["results"]["primary"]
        print("Against today's scoring (same tickets, one by one):")
        for name, r in out["results"].items():
            if name != "primary":
                c = ex.paired(base, r)
                print(f"  {name:42s} better on {c['b_only']:3d}, worse on {c['a_only']:3d}   p = {c['p']:.3f}")
        print()
        if days == ar.ACCURACY_DAYS:
            print("── By domain (Top-3 hit %, most tickets first) ──")
            print(out["by_domain"].to_string(index=False), "\n")

    print("── Domain families from co-occurrence (review by hand) ──")
    print(ex.domain_families(t).to_string(index=False))


if __name__ == "__main__":
    main()
