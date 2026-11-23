"""Fixed-order pipeline for one supplier email, using the specialists in specialists.py.

    1. The supply analyst reads the email and returns its brief.
    2. A customer notice for each order at risk (skipped if there are none).
    3. A reply to the supplier.

Every draft goes through the review step (writer, reviewer with at most 2 revisions, name
check, saved in week-05/drafts/, marked for a person when needed). At the end it prints a
summary: drafts saved, drafts marked for a person, decisions for a person, and total tokens.

Usage (from the top of the repository):
    .venv/bin/python week-05/pipeline.py --email week-05/brookfield-bikes/supplier-emails/01-northgate-controller-delay.eml
    .venv/bin/python week-05/pipeline.py --email <file.eml> --save run.txt

--save also writes everything printed to the named text file (UTF-8).
"""

import argparse
import sys
from pathlib import Path

import specialists as sp


class Tee:
    """Write to the terminal and to a file at the same time."""

    def __init__(self, stream, file):
        self.stream, self.file = stream, file

    def write(self, text):
        self.stream.write(text)
        self.file.write(text)
        return len(text)

    def flush(self):
        self.stream.flush()
        self.file.flush()


def run(email_path):
    email = email_path.read_text(encoding="utf-8")
    print(f"Pipeline for {email_path}")
    print(f"Model: {sp.MODEL}")

    print("\n=== Step 1: supply analyst")
    brief = sp.run_analyst(email)

    results = []
    at_risk = brief["orders_at_risk"]
    if at_risk:
        print(f"\n=== Step 2: customer notices ({len(at_risk)} order(s) at risk)")
        for o in at_risk:
            results.append(sp.customer_notice_step(o["order_id"], brief))
    else:
        print("\n=== Step 2: customer notices - skipped, no orders at risk")

    print("\n=== Step 3: supplier reply")
    results.append(sp.supplier_reply_step(email, brief))

    summary(brief, results)


def summary(brief, results):
    saved = [r for r in results if r["path"]]
    marked = [r for r in saved if r["needs_person"]]

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    print(f"\nDrafts saved: {len(saved)}")
    for r in saved:
        print(f"  {r['kind']:<9}{r['key']:<8}{r['verdict']:<8}"
              f"{r['revisions']} revision(s)  {Path(r['path']).relative_to(sp.HERE.parent)}")
    for r in results:
        if not r["path"]:
            print(f"  {r['kind']:<9}{r['key']:<8}not saved: no reply needed")

    print(f"\nDrafts marked for a person: {len(marked)}")
    for r in marked:
        print(f"  {r['kind']} {r['key']}: {'; '.join(r['reasons'])}")

    decisions = brief["decisions_for_a_person"]
    print(f"\nDecisions for a person: {len(decisions)}")
    for d in decisions:
        print(f"  - {d}")

    sp.token_report()


def main():
    parser = argparse.ArgumentParser(description="Run Brookfield's specialists on one supplier email.")
    parser.add_argument("--email", required=True, type=Path, help="path of one supplier email")
    parser.add_argument("--save", metavar="FILE", type=Path,
                        help="also write everything printed to this text file (UTF-8)")
    args = parser.parse_args()
    if not args.email.is_file():
        sys.exit(f"No such email file: {args.email}")

    if not args.save:
        run(args.email)
        return

    args.save.parent.mkdir(parents=True, exist_ok=True)
    with args.save.open("w", encoding="utf-8") as f:
        out, err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = Tee(out, f), Tee(err, f)
        try:
            run(args.email)
        finally:
            sys.stdout, sys.stderr = out, err
    print(f"\nSaved the output to {args.save}")


if __name__ == "__main__":
    main()
