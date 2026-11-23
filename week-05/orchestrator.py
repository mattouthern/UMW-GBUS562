"""Orchestrator for one supplier email: Claude decides which specialists to use.

An agent (claude-sonnet-5) gets the system prompt in brookfield-bikes/prompts/orchestrator.txt
and four tools:
    run_supply_analyst                  runs the analyst from specialists.py
    draft_supplier_reply                drafts the supplier reply (needs the analyst's brief)
    draft_customer_notice(order_id)     drafts a notice for one order at risk
    finish(summary, decision_needed)    ends the job
Each draft goes through the same review step as the pipeline. The agent stops after 10 tool
calls. Each choice it makes is printed, then the same summary as pipeline.py.

Usage (from the top of the repository):
    .venv/bin/python week-05/orchestrator.py --email week-05/brookfield-bikes/supplier-emails/02-kendall-tire-options.eml
    .venv/bin/python week-05/orchestrator.py --email <file.eml> --save run.txt

--save also writes everything printed to the named text file (UTF-8).
"""

import argparse
import json
import sys
from pathlib import Path

import specialists as sp
from pipeline import Tee, summary

MAX_TOOL_CALLS = 10

TOOLS = [
    {
        "name": "run_supply_analyst",
        "description": (
            "Run the supply analyst on the supplier email. It looks up the part, the open "
            "orders that need it, and coverage under the new schedule, and returns a brief: "
            "part_id, what_changed, orders_at_risk, options, decisions_for_a_person, "
            "supplier_questions. The drafting tools need this brief."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "draft_supplier_reply",
        "description": (
            "Draft a reply to the supplier from the email and the analyst's brief (customer "
            "names are removed first). The draft is reviewed, checked, and saved for a person. "
            "Returns the reviewer's verdict, whether it is marked for a person, and the draft. "
            "Run the analyst first."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "draft_customer_notice",
        "description": (
            "Draft a notice to the customer of one order at risk, given only that order's "
            "facts. The order must be in the analyst's orders_at_risk. The draft is reviewed, "
            "checked, and saved for a person. Call once per order."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "Order ID, such as O-1043."}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "finish",
        "description": (
            "End the job. Give a short summary for Brookfield's purchasing manager and say "
            "whether a person must make a decision."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Short summary for the purchasing manager."},
                "decision_needed": {"type": "boolean",
                                    "description": "True if a person must make a decision."},
            },
            "required": ["summary", "decision_needed"],
            "additionalProperties": False,
        },
    },
]


def draft_result(r):
    """What the orchestrator sees after a draft."""
    return {"verdict": r["verdict"], "revisions": r["revisions"],
            "marked_for_a_person": r["needs_person"], "reasons": r["reasons"],
            "saved": bool(r["path"]), "draft": r["draft"]}


class Job:
    def __init__(self, email):
        self.email = email
        self.brief = None
        self.results = {}  # ("supplier", part) or ("customer", order_id) -> step result
        self.finish = None

    def run_tool(self, name, args):
        """Run one tool. Returns (result text, is_error)."""
        if name == "run_supply_analyst":
            if self.brief is None:
                self.brief = sp.run_analyst(self.email)
                return json.dumps(self.brief), False
            return json.dumps({"note": "The analyst already ran; here is its brief.",
                               "brief": self.brief}), False

        if name in ("draft_supplier_reply", "draft_customer_notice") and self.brief is None:
            return "Run run_supply_analyst first: the drafts need its brief.", True

        if name == "draft_supplier_reply":
            key = ("supplier", self.brief["part_id"])
            if key in self.results:
                return json.dumps({"note": "Already drafted.", **draft_result(self.results[key])}), False
            self.results[key] = sp.supplier_reply_step(self.email, self.brief)
            return json.dumps(draft_result(self.results[key])), False

        if name == "draft_customer_notice":
            order_id = str(args.get("order_id", "")).strip().upper()
            key = ("customer", order_id)
            if key in self.results:
                return json.dumps({"note": "Already drafted.", **draft_result(self.results[key])}), False
            try:
                self.results[key] = sp.customer_notice_step(order_id, self.brief)
            except ValueError as e:
                return str(e), True
            return json.dumps(draft_result(self.results[key])), False

        if name == "finish":
            self.finish = {"summary": args.get("summary", ""),
                           "decision_needed": bool(args.get("decision_needed"))}
            return "Finished.", False

        return f"Unknown tool: {name}", True


def run(email_path):
    email = email_path.read_text(encoding="utf-8")
    print(f"Orchestrator for {email_path}")
    print(f"Model: {sp.MODEL}")

    job = Job(email)
    messages = [{"role": "user", "content":
                 "Handle this supplier email. It is between the <email> tags.\n\n"
                 f"<email>\n{email.strip()}\n</email>"}]
    calls = 0
    stop_why = None

    while job.finish is None and stop_why is None:
        system = sp.prompt("orchestrator")  # read fresh each turn
        response = sp.call_model("orchestrator", system, messages, tools=TOOLS)
        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(f"\nOrchestrator: {block.text.strip()}")
        if response.stop_reason != "tool_use":
            stop_why = f"the orchestrator stopped without calling finish ({response.stop_reason})"
            break

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in (b for b in response.content if b.type == "tool_use"):
            if calls >= MAX_TOOL_CALLS or job.finish is not None:
                text, is_error = "Not run: the job has ended.", True
            else:
                calls += 1
                args = f" {json.dumps(block.input)}" if block.input else ""
                print(f"\n### CHOICE {calls}/{MAX_TOOL_CALLS}: {block.name}{args}", flush=True)
                text, is_error = job.run_tool(block.name, block.input)
                if is_error:
                    print(f"    tool error: {text}")
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": text, "is_error": is_error})
        messages.append({"role": "user", "content": results})
        if job.finish is None and calls >= MAX_TOOL_CALLS:
            stop_why = f"reached the limit of {MAX_TOOL_CALLS} tool calls without finish"

    print("\n" + "=" * 60)
    print("ORCHESTRATOR'S FINISH")
    print("=" * 60)
    if job.finish:
        print(f"\nDecision needed by a person: {'YES' if job.finish['decision_needed'] else 'no'}")
        print(f"\n{job.finish['summary']}")
    else:
        print(f"\nNo finish: {stop_why}.")
    print(f"\nTool calls used: {calls}/{MAX_TOOL_CALLS}")

    brief = job.brief or {"decisions_for_a_person": []}
    summary(brief, list(job.results.values()))


def main():
    parser = argparse.ArgumentParser(description="Let Claude coordinate Brookfield's specialists on one supplier email.")
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
