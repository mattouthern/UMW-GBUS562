"""Four specialists that the coordinators (pipeline.py, orchestrator.py) share.

    run_analyst(email)                   agent with the supply MCP tools; returns its brief (dict)
    write_supplier_reply(email, brief)   one model call; customer names are removed first
    write_customer_notice(order)         one model call, given only that one order's facts
    review(draft, facts, recipient)      one model call; returns APPROVE or REVISE with reasons

The review step runs a writer, then the reviewer, revising at most MAX_REVISIONS times. Then
code checks that no customer name is in a supplier draft and no supplier name is in a
customer draft, saves the draft in week-05/drafts/<company folder>/, and marks it for a
person if it failed either check or its order has a late penalty. Then it prints the final
draft once, between a FINAL DRAFT TO SUPPLIER / FINAL DRAFT TO CUSTOMER <order id> line and
a "Marked for a person: yes/no" line:

    supplier_reply_step(email, brief)    -> result dict (draft, path, needs_person, reasons)
    customer_notice_step(order_id, brief) -> result dict

The company comes from set_org(folder): its CSV files (column names worked out from each
header row, see org_data.py) and its prompts/ folder, which every specialist reads its
system prompt from on each run. Without set_org, week-05/brookfield-bikes is used.
The analyst's brief is printed in full. Every handoff and each specialist's token use is
printed; token_report() prints the totals.

The Anthropic API key is read from the API_KEY environment variable (a Codespaces secret).
It is never printed or written to a file.

Usage from a coordinator (run from the top of the repository with the .venv Python):

    import specialists as sp
    sp.set_org("copperline-coffee")          # optional; default brookfield-bikes
    email = Path("week-05/copperline-coffee/supplier-emails/01-danforth-pump-delay.eml").read_text()
    brief = sp.run_analyst(email)
    sp.supplier_reply_step(email, brief)
    for o in brief["orders_at_risk"]:
        sp.customer_notice_step(o["order_id"], brief)
    sp.token_report()

Or try it on one email:  .venv/bin/python week-05/specialists.py [--org FOLDER] <email.eml>
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import anthropic
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

import org_data
from org_data import OrgData, OrgError

MODEL = "claude-sonnet-5"
MAX_TOOL_CALLS = 10
MAX_REVISIONS = 2
MAX_TOKENS = 16000

HERE = Path(__file__).resolve().parent
DATA = PROMPTS = DRAFTS = None  # set by set_org()
SERVER = HERE / "supply_mcp_server.py"
SERVER_LOG = HERE / "logs" / "mcp-server.log"

_client = None
TOKENS = {}  # specialist name -> {"calls", "input", "output"}

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "part_id": {"type": "string"},
        "what_changed": {"type": "string"},
        "orders_at_risk": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "customer": {"type": "string"},
                    "units_short": {"type": "integer"},
                    "ship_day": {"type": "integer"},
                    "ready_day": {"type": ["integer", "null"]},
                    "late_penalty": {"type": "boolean"},
                },
                "required": ["order_id", "customer", "units_short", "ship_day",
                             "ready_day", "late_penalty"],
                "additionalProperties": False,
            },
        },
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "order_ids_at_risk": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "order_ids_at_risk"],
                "additionalProperties": False,
            },
        },
        "decisions_for_a_person": {"type": "array", "items": {"type": "string"}},
        "supplier_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["part_id", "what_changed", "orders_at_risk", "options",
                 "decisions_for_a_person", "supplier_questions"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def client():
    """One Anthropic client, using the key in API_KEY (never printed or saved)."""
    global _client
    if _client is None:
        key = os.environ.get("API_KEY")
        if not key:
            sys.exit("API_KEY is not set. Add it as a Codespaces secret and restart the codespace.")
        _client = anthropic.Anthropic(api_key=key)
    return _client


def set_org(folder=None):
    """Use one company folder (a path, or a folder name in week-05; None = brookfield-bikes)
    for the data, the prompts, and where drafts are saved. Exits with a message if the
    folder is incomplete or its columns can't be worked out."""
    global DATA, PROMPTS, DRAFTS
    try:
        DATA = org_data.resolve_org(folder)
        data = OrgData(DATA)
    except OrgError as e:
        sys.exit(str(e))
    PROMPTS = DATA / "prompts"
    DRAFTS = HERE / "drafts" / DATA.name
    print(f"Company folder: {show(DATA)}")
    for line in data.describe_columns():
        print(f"  columns in {line}")
    return DATA


def show(path):
    """A path relative to the top of the repository when it is inside it."""
    path = Path(path)
    return path.relative_to(HERE.parent) if path.is_relative_to(HERE.parent) else path


def _org():
    if DATA is None:
        set_org()
    return DATA


def data():
    """The company's CSV files, read fresh."""
    return OrgData(_org())


def prompt(name):
    """Read a specialist's system prompt fresh on every run."""
    return (_org() / "prompts" / f"{name}.txt").read_text(encoding="utf-8").strip()


def customer_names():
    return data().customer_names()


def supplier_names():
    return data().supplier_names()


def name_variants(name):
    """The name, plus the name without trailing punctuation ("Bayside Cycle Co." -> "...Co")."""
    return {name, name.rstrip(".,")}


def names_found(text, names):
    low = text.lower()
    return [n for n in names if any(v.lower() in low for v in name_variants(n))]


def handoff(src, dst, what):
    print(f"\n>>> HANDOFF  {src} -> {dst}: {what}", flush=True)


def record_tokens(specialist, response):
    t = TOKENS.setdefault(specialist, {"calls": 0, "input": 0, "output": 0})
    t["calls"] += 1
    t["input"] += response.usage.input_tokens
    t["output"] += response.usage.output_tokens
    print(f"    [tokens] {specialist}: {response.usage.input_tokens} in, "
          f"{response.usage.output_tokens} out (call {t['calls']}; running total "
          f"{t['input']} in, {t['output']} out)", flush=True)


def token_report():
    """Print the tokens each specialist used so far."""
    print("\nTokens used by each specialist")
    print(f"  {'specialist':<16}{'calls':>6}{'input':>10}{'output':>10}")
    total_in = total_out = 0
    for name, t in TOKENS.items():
        print(f"  {name:<16}{t['calls']:>6}{t['input']:>10}{t['output']:>10}")
        total_in += t["input"]
        total_out += t["output"]
    print(f"  {'total':<16}{'':>6}{total_in:>10}{total_out:>10}")
    return TOKENS


def call_model(specialist, system, messages, **kwargs):
    """One Messages API call with error handling and token accounting."""
    try:
        response = client().messages.create(
            model=MODEL, max_tokens=MAX_TOKENS, system=system, messages=messages, **kwargs)
    except anthropic.AuthenticationError:
        sys.exit("Claude rejected the API key in API_KEY.")
    except anthropic.NotFoundError:
        sys.exit(f"Model '{MODEL}' was not found.")
    except anthropic.RateLimitError:
        sys.exit("Rate limit reached. Wait a moment and try again.")
    except anthropic.APIStatusError as e:
        sys.exit(f"Claude API error ({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        sys.exit("Could not connect to the Claude API.")
    record_tokens(specialist, response)
    if response.stop_reason == "refusal":
        raise RuntimeError(f"{specialist}: Claude declined the request (refusal).")
    if response.stop_reason == "max_tokens":
        raise RuntimeError(f"{specialist}: the reply hit max_tokens before finishing.")
    return response


def text_of(response):
    return "\n".join(b.text for b in response.content if b.type == "text").strip()


# ---------------------------------------------------------------------------
# Specialist 1: the supply analyst (an agent with the MCP tools)
# ---------------------------------------------------------------------------

def _mcp_to_claude_tool(tool):
    schema = dict(tool.input_schema or {})
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return {"name": tool.name, "description": tool.description or "", "input_schema": schema}


async def _call_mcp_tool(session, name, args):
    result = await session.call_tool(name, args)
    text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
    if not text and result.structured_content is not None:
        text = json.dumps(result.structured_content)
    return text, bool(result.is_error)


async def run_analyst_async(email):
    """The analyst agent loop. Stops after MAX_TOOL_CALLS tool calls. Returns the brief."""
    handoff("coordinator", "analyst", f"supplier email ({len(email)} chars)")
    system = prompt("analyst")
    messages = [{"role": "user", "content":
                 "Analyze this supplier email. It is between the <email> tags.\n\n"
                 f"<email>\n{email.strip()}\n</email>"}]
    fmt = {"format": {"type": "json_schema", "schema": BRIEF_SCHEMA}}
    calls = 0

    server = StdioServerParameters(command=sys.executable, args=[str(SERVER), "--org", str(_org())])
    SERVER_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SERVER_LOG.open("a", encoding="utf-8") as errlog:
        async with stdio_client(server, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                mcp_tools = (await session.list_tools()).tools
                tools = [_mcp_to_claude_tool(t) for t in mcp_tools]
                names = {t["name"] for t in tools}

                while True:
                    limit_hit = calls >= MAX_TOOL_CALLS
                    extra = {"tool_choice": {"type": "none"}} if limit_hit else {}
                    response = call_model("analyst", system, messages, tools=tools,
                                          output_config=fmt, **extra)
                    if response.stop_reason != "tool_use":
                        break
                    messages.append({"role": "assistant", "content": response.content})
                    results = []
                    for block in (b for b in response.content if b.type == "tool_use"):
                        if calls >= MAX_TOOL_CALLS:
                            text, is_error = (f"Tool limit of {MAX_TOOL_CALLS} calls reached. "
                                              "Give your brief now with the numbers you have."), True
                        elif block.name not in names:
                            text, is_error = f"Unknown tool: {block.name}", True
                        else:
                            calls += 1
                            print(f"    analyst tool call {calls}/{MAX_TOOL_CALLS}: "
                                  f"{block.name} {json.dumps(block.input)}", flush=True)
                            text, is_error = await _call_mcp_tool(session, block.name, block.input)
                        results.append({"type": "tool_result", "tool_use_id": block.id,
                                        "content": text, "is_error": is_error})
                    messages.append({"role": "user", "content": results})
                    if calls >= MAX_TOOL_CALLS:
                        print(f"    analyst reached {MAX_TOOL_CALLS} tool calls; asking for the brief.")

    brief = json.loads(text_of(response))
    handoff("analyst", "coordinator", f"brief for {brief['part_id']}, "
            f"{len(brief['orders_at_risk'])} order(s) at risk, {calls} tool call(s)")
    print("\nANALYST'S BRIEF")
    print(json.dumps(brief, indent=2, ensure_ascii=False), flush=True)
    return brief


def run_analyst(email):
    """Run the analyst on one supplier email. Returns its brief as a dict (JSON)."""
    return asyncio.run(run_analyst_async(email))


# ---------------------------------------------------------------------------
# Specialist 2: supplier-reply writer (one call; customer names removed first)
# ---------------------------------------------------------------------------

def remove_customer_names(obj, names=None):
    """Return a copy of obj (str, list, dict) with every customer name replaced and every
    'customer' field dropped."""
    names = names if names is not None else customer_names()
    if isinstance(obj, dict):
        return {k: remove_customer_names(v, names) for k, v in obj.items() if k != "customer"}
    if isinstance(obj, list):
        return [remove_customer_names(v, names) for v in obj]
    if isinstance(obj, str):
        for n in names:
            for v in name_variants(n):
                obj = re.sub(re.escape(v), "[a customer]", obj, flags=re.IGNORECASE)
        return obj
    return obj


def supplier_facts(email, brief):
    return {"supplier_email": remove_customer_names(email.strip()),
            "brief": remove_customer_names(brief)}


def write_supplier_reply(email, brief, previous=None, feedback=None):
    """One model call. Customer names are removed from the email and brief first.
    previous/feedback are used when the reviewer asked for a revision."""
    facts = supplier_facts(email, brief)
    handoff("coordinator", "supplier_writer",
            "email + brief with customer names removed" + (" (revision)" if feedback else ""))
    content = ("The supplier's email is between the <email> tags and the analyst's brief between "
               f"the <brief> tags.\n\n<email>\n{facts['supplier_email']}\n</email>\n\n"
               f"<brief>\n{json.dumps(facts['brief'], indent=2)}\n</brief>")
    content += _revision_note(previous, feedback)
    draft = text_of(call_model("supplier_writer", prompt("supplier-writer"),
                               [{"role": "user", "content": content}]))
    handoff("supplier_writer", "coordinator", f"supplier draft ({len(draft)} chars)")
    return draft


# ---------------------------------------------------------------------------
# Specialist 3: customer-notice writer (one call, one order's facts only)
# ---------------------------------------------------------------------------

def order_facts(order_id, brief):
    """The facts of one order: orders.csv joined with the analyst's numbers for it."""
    row = data().order(order_id)
    risk = next((o for o in brief["orders_at_risk"]
                 if o["order_id"].strip().upper() == order_id.upper()), None)
    if row is None or risk is None:
        raise ValueError(f"Order {order_id} is not in orders.csv or not at risk in the brief.")
    ready = risk["ready_day"]
    return {
        "order_id": row["order_id"],
        "customer": row["customer"],
        "product": row["product"],
        "quantity": row["quantity"],
        "planned_ship_day": risk["ship_day"],
        "expected_ship_day": ready,
        "days_late": None if ready is None else ready - risk["ship_day"],
        "late_penalty": risk["late_penalty"],
    }


def _customer_visible(order):
    """What the writer and reviewer may see: no penalty or contract terms."""
    return {k: v for k, v in order.items() if k != "late_penalty"}


def write_customer_notice(order, previous=None, feedback=None):
    """One model call, given only this one order's facts (see order_facts)."""
    facts = _customer_visible(order)
    handoff("coordinator", "customer_writer",
            f"facts for order {order['order_id']} only" + (" (revision)" if feedback else ""))
    content = ("The order's facts are between the <order> tags. Days are counted from today. "
               "expected_ship_day null means no ship date is known yet.\n\n"
               f"<order>\n{json.dumps(facts, indent=2)}\n</order>")
    content += _revision_note(previous, feedback)
    draft = text_of(call_model("customer_writer", prompt("customer-writer"),
                               [{"role": "user", "content": content}]))
    handoff("customer_writer", "coordinator", f"notice for {order['order_id']} ({len(draft)} chars)")
    return draft


def _revision_note(previous, feedback):
    if not feedback:
        return ""
    return ("\n\nYour previous draft is between the <previous_draft> tags. A reviewer asked for "
            "changes, between the <review> tags. Write the full revised draft.\n\n"
            f"<previous_draft>\n{previous}\n</previous_draft>\n\n<review>\n{feedback}\n</review>")


# ---------------------------------------------------------------------------
# Specialist 4: reviewer (one call)
# ---------------------------------------------------------------------------

def review(draft, facts, recipient):
    """One model call. recipient is 'supplier' or 'customer'.
    Returns {"verdict": "APPROVE" | "REVISE", "reasons": str}."""
    handoff("coordinator", "reviewer", f"{recipient} draft + facts")
    content = (f"This draft goes to a {recipient}.\n\n<draft>\n{draft}\n</draft>\n\n"
               f"<facts>\n{json.dumps(facts, indent=2)}\n</facts>")
    answer = text_of(call_model("reviewer", prompt("reviewer"), [{"role": "user", "content": content}]))
    first, _, rest = answer.partition("\n")
    verdict = "APPROVE" if first.strip().upper().startswith("APPROVE") else "REVISE"
    result = {"verdict": verdict, "reasons": rest.strip() if verdict == "REVISE" else ""}
    handoff("reviewer", "coordinator", verdict)
    if result["reasons"]:
        print("    " + result["reasons"].replace("\n", "\n    "))
    return result


# ---------------------------------------------------------------------------
# The review step: writer -> reviewer (revise at most twice) -> name check -> save
# ---------------------------------------------------------------------------

def _review_loop(recipient, facts, write):
    """write(previous, feedback) -> draft. Returns (draft, last review, revisions)."""
    draft = write(None, None)
    if draft.strip() == "NO REPLY NEEDED":
        return draft, {"verdict": "APPROVE", "reasons": ""}, 0
    revisions = 0
    while True:
        verdict = review(draft, facts, recipient)
        if verdict["verdict"] == "APPROVE" or revisions >= MAX_REVISIONS:
            return draft, verdict, revisions
        revisions += 1
        print(f"    revision {revisions}/{MAX_REVISIONS}")
        draft = write(draft, verdict["reasons"])


def print_final(kind, key, draft, needs_person):
    """Print the final draft once, after its review step."""
    head = "FINAL DRAFT TO SUPPLIER" if kind == "supplier" else f"FINAL DRAFT TO CUSTOMER {key}"
    print(f"\n{head}\n{draft.strip()}\nMarked for a person: {'yes' if needs_person else 'no'}",
          flush=True)


def _save(kind, key, recipient, draft, verdict, revisions, needs_person, reasons):
    _org()
    DRAFTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = DRAFTS / f"{stamp}-{kind}-{key}.md"
    lines = [
        f"# {kind.title()} draft: {key}",
        "",
        f"- To: {recipient}",
        f"- Needs a person: {'YES' if needs_person else 'no'}",
    ]
    lines += [f"  - {r}" for r in reasons]
    lines += [f"- Reviewer: {verdict['verdict']} after {revisions} revision(s)", ""]
    if verdict["reasons"]:
        lines += ["Reviewer's last notes:", "", verdict["reasons"], ""]
    lines += ["---", "", draft, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"    saved {show(path)}"
          f"{'  [MARKED FOR A PERSON]' if needs_person else ''}")
    return path


def _finish(kind, key, recipient, draft, verdict, revisions, forbidden, forbidden_label, penalty):
    reasons = []
    if verdict["verdict"] != "APPROVE":
        reasons.append(f"reviewer did not approve after {revisions} revision(s)")
    leaked = names_found(draft, forbidden)
    if leaked:
        reasons.append(f"names a {forbidden_label}: {', '.join(leaked)}")
    print(f"    name check ({forbidden_label} names): {'FAIL ' + ', '.join(leaked) if leaked else 'pass'}")
    if penalty:
        reasons.append(penalty)
    path = _save(kind, key, recipient, draft, verdict, revisions, bool(reasons), reasons)
    print_final(kind, key, draft, bool(reasons))
    return {"kind": kind, "key": key, "draft": draft, "verdict": verdict["verdict"],
            "revisions": revisions, "needs_person": bool(reasons), "reasons": reasons,
            "path": str(path)}


def supplier_reply_step(email, brief):
    """Write, review, check, and save the supplier reply."""
    facts = supplier_facts(email, brief)
    draft, verdict, revisions = _review_loop(
        "supplier", facts, lambda prev, fb: write_supplier_reply(email, brief, prev, fb))
    if draft.strip() == "NO REPLY NEEDED":
        print("    supplier writer: no reply needed; nothing saved.")
        print_final("supplier", brief["part_id"], draft, False)
        return {"kind": "supplier", "key": brief["part_id"], "draft": draft, "verdict": "APPROVE",
                "revisions": 0, "needs_person": False, "reasons": [], "path": None}
    penalties = [o["order_id"] for o in brief["orders_at_risk"] if o["late_penalty"]]
    penalty = f"order(s) with a late penalty: {', '.join(penalties)}" if penalties else None
    return _finish("supplier", brief["part_id"], "supplier", draft, verdict, revisions,
                   customer_names(), "customer", penalty)


def customer_notice_step(order_id, brief):
    """Write, review, check, and save the notice for one order at risk."""
    order = order_facts(order_id, brief)
    draft, verdict, revisions = _review_loop(
        "customer", _customer_visible(order), lambda prev, fb: write_customer_notice(order, prev, fb))
    penalty = f"order {order_id} has a late penalty" if order["late_penalty"] else None
    order_id = order["order_id"]
    return _finish("customer", order_id, f"customer {order['customer']}", draft, verdict, revisions,
                   supplier_names(), "supplier", penalty)


# ---------------------------------------------------------------------------
# Try it on one email
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run every specialist on one supplier email.")
    parser.add_argument("email", type=Path, help="path of one supplier email")
    org_data.add_org_argument(parser)
    args = parser.parse_args()
    if not args.email.is_file():
        sys.exit(f"No such email file: {args.email}")
    set_org(args.org)
    email = args.email.read_text(encoding="utf-8")
    brief = run_analyst(email)
    results = [supplier_reply_step(email, brief)]
    results += [customer_notice_step(o["order_id"], brief) for o in brief["orders_at_risk"]]
    print("\nDrafts")
    for r in results:
        flag = "NEEDS A PERSON" if r["needs_person"] else "ok"
        print(f"  {r['kind']:<9}{r['key']:<8}{flag:<16}{r['path'] or '(no reply needed)'}")
    token_report()


if __name__ == "__main__":
    main()
