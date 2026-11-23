## What I Built
My week-05 team prepares a company's response to a supplier email. A supply analyst uses three read-only tools from an MCP server (get_part, get_orders_for_part, check_coverage) to write a brief on which orders are at risk, a customer writer drafts a delay notice for each affected order, a supplier writer drafts the reply to the supplier with customer names removed, and a reviewer approves or sends back each draft. I can run the team two ways: a pipeline that always runs the steps in the same order, or an orchestrator that lets Claude choose which specialist to call next. To make it work for Copperline Coffee Machines, Claude Code added an --org option to the MCP server, the specialists, the pipeline and the orchestrator, so each one reads a company folder with its own CSV files and system prompts and saves drafts in drafts/<company folder>/. Copperline's CSV columns have different names from Brookfield's (for example "units" and "ship_by_day" instead of quantity and ship day), so Claude Code added org_data.py, which works out which column is which from the header row instead of hard-coding the names. Using Claude Code, I ran all four Copperline supplier emails through both the pipeline and the orchestrator, and the eight runs are saved in runs/copperline-01-pipeline.txt through runs/copperline-04-orchestrator.txt.

## Why This Approach
Task A: Agent, because each leaking-machine email is different, so the model has to decide whether it needs to look up the order, the warranty terms, or both before it can write the reply.

Task B: Team, because the job has separate parts that need different skills: finding the shipped machines in the data, writing a notice to each customer, and writing a formal report to the regulator, and each draft should be checked by a reviewer before a person sends it.

Task C: Pipeline, because every wholesale order goes through the same steps in the same order, so there is nothing for a coordinator to decide, and a fixed order is cheaper and easier to check.

Task D: Orchestrator, because each email needs a different mix of specialists and new kinds of email keep arriving, so a fixed order of steps can't cover them all and Claude has to choose the specialists for each email.

## Example Run
```
Orchestrator for week-05/copperline-coffee/supplier-emails/01-danforth-pump-delay.eml
Company folder: week-05/copperline-coffee
  columns in orders.csv: product = 'product', quantity = 'units', ship_day = 'ship_by_day'
  columns in bill-of-materials.csv: product = 'product', parts_per_unit = 'per_unit'
Model: claude-sonnet-5
    [tokens] orchestrator: 1528 in, 81 out (call 1; running total 1528 in, 81 out)

Orchestrator: This email requires action — a supply shortfall affecting scheduled deliveries. Let me start by running the supply analyst to understand the impact on customer orders.

### CHOICE 1/10: run_supply_analyst

>>> HANDOFF  coordinator -> analyst: supplier email (696 chars)
    [tokens] analyst: 2637 in, 125 out (call 1; running total 2637 in, 125 out)
    analyst tool call 1/10: get_part {"part_id": "PU-20"}
    analyst tool call 2/10: get_orders_for_part {"part_id": "PU-20"}
    [tokens] analyst: 3787 in, 143 out (call 2; running total 6424 in, 268 out)
    analyst tool call 3/10: check_coverage {"part_id": "PU-20", "arrivals": [{"qty": 40, "day": 4}, {"qty": 160, "day": 19}]}
    [tokens] analyst: 4987 in, 655 out (call 3; running total 11411 in, 923 out)

>>> HANDOFF  analyst -> coordinator: brief for PU-20, 3 order(s) at risk, 3 tool call(s)

ANALYST'S BRIEF
{
  "part_id": "PU-20",
  "what_changed": "Danforth will deliver 40 of the 200 PU-20 pumps on day 4 as scheduled, with the remaining 160 arriving 15 days later on day 19.",
  "orders_at_risk": [
    {
      "order_id": "O-2203",
      "customer": "Elm State University Dining",
      "units_short": 10,
      "ship_day": 12,
      "ready_day": 19,
      "late_penalty": true
    },
    {
      "order_id": "O-2204",
      "customer": "Kettle & Crumb Bakery",
      "units_short": 20,
      "ship_day": 15,
      "ready_day": 19,
      "late_penalty": false
    },
    {
      "order_id": "O-2205",
      "customer": "Tall Pines Coffee House",
      "units_short": 25,
      "ship_day": 17,
      "ready_day": 19,
      "late_penalty": true
    }
  ],
  "options": [],
  "decisions_for_a_person": [
    "Decide whether to notify Elm State University Dining and Tall Pines Coffee House (both have late penalties) about the expected 2-7 day delay.",
    "Decide whether to re-sequence shipments to prioritize late-penalty orders (O-2203, O-2205) ahead of O-2204 once the day-19 shipment arrives.",
    "Decide whether to seek an expedited partial shipment from Danforth to cover the shortfall before day 19.",
    "Decide what information to send Danforth in response to their request (customer names and quantities for PO-5512)."
  ],
  "supplier_questions": [
    "Could you send us a list of the customer orders these pumps are for, with each customer's name and quantity?"
  ]
}
    [tokens] orchestrator: 2138 in, 209 out (call 2; running total 3666 in, 290 out)

Orchestrator: Now I'll draft the supplier reply (note: we should not disclose customer names per privacy practice, but the reply draft process handles that by removing names) and notify the two customers with at-risk orders that have late penalties.

### CHOICE 2/10: draft_supplier_reply

>>> HANDOFF  coordinator -> supplier_writer: email + brief with customer names removed
    [tokens] supplier_writer: 1146 in, 296 out (call 1; running total 1146 in, 296 out)

>>> HANDOFF  supplier_writer -> coordinator: supplier draft (827 chars)

>>> HANDOFF  coordinator -> reviewer: supplier draft + facts
    [tokens] reviewer: 1505 in, 1034 out (call 1; running total 1505 in, 1034 out)

>>> HANDOFF  reviewer -> coordinator: APPROVE
    name check (customer names): pass
    saved week-05/drafts/copperline-coffee/20261123-212704-supplier-PU-20.md  [MARKED FOR A PERSON]

FINAL DRAFT TO SUPPLIER
Subject: RE: PO-5512 - PU-20 pumps - partial shipment

Hello Diane,

Thank you for the update. To confirm our understanding: 40 of the 200 PU-20 pumps will ship on the scheduled date, with the remaining 160 arriving 15 days later.

This shortfall affects 55 units across our orders and will delay fulfillment by up to 7 days on the affected orders.

We're not able to share customer names or order details, but we appreciate your effort to minimize the delay. To help reduce the impact, could you let us know if any portion of the remaining 160 units could be expedited or shipped in smaller batches sooner than the 15-day mark? Any earlier partial delivery would help us reduce the delay on our end.

If a decision is needed from us, we'll confirm by your stated timeline.

Best regards,
Purchasing, Copperline Coffee Machines
Marked for a person: yes

### CHOICE 3/10: draft_customer_notice {"order_id": "O-2203"}

>>> HANDOFF  coordinator -> customer_writer: facts for order O-2203 only
    [tokens] customer_writer: 363 in, 150 out (call 1; running total 363 in, 150 out)

>>> HANDOFF  customer_writer -> coordinator: notice for O-2203 (431 chars)

>>> HANDOFF  coordinator -> reviewer: customer draft + facts
    [tokens] reviewer: 588 in, 287 out (call 2; running total 2093 in, 1321 out)

>>> HANDOFF  reviewer -> coordinator: APPROVE
    name check (supplier names): pass
    saved week-05/drafts/copperline-coffee/20261123-212710-customer-O-2203.md  [MARKED FOR A PERSON]

FINAL DRAFT TO CUSTOMER O-2203
Dear Elm State University Dining,

We're writing about order O-2203 for 50 Cafe One units. This order was originally planned to ship in 12 days, but it is now expected to ship in 19 days — 7 days later than planned.

We apologize for this delay. It is due to a delay in a part from one of our suppliers.

Thank you for your patience, and we'll keep you updated on the order's progress.

Customer Service, Copperline Coffee Machines
Marked for a person: yes

### CHOICE 4/10: draft_customer_notice {"order_id": "O-2205"}

>>> HANDOFF  coordinator -> customer_writer: facts for order O-2205 only
    [tokens] customer_writer: 366 in, 174 out (call 2; running total 729 in, 324 out)

>>> HANDOFF  customer_writer -> coordinator: notice for O-2205 (495 chars)

>>> HANDOFF  coordinator -> reviewer: customer draft + facts
    [tokens] reviewer: 615 in, 284 out (call 3; running total 2708 in, 1605 out)

>>> HANDOFF  reviewer -> coordinator: APPROVE
    name check (supplier names): pass
    saved week-05/drafts/copperline-coffee/20261123-212716-customer-O-2205.md  [MARKED FOR A PERSON]

FINAL DRAFT TO CUSTOMER O-2205
Subject: Update on Your Order O-2205

Dear Tall Pines Coffee House,

We're writing about your order O-2205 for 25 units of the Cafe One. This order was originally planned to ship on day 17, but it is now expected to ship on day 19 — 2 days later than planned.

We apologize for this delay. It is due to a delay in a part from one of our suppliers.

We will keep you informed of any further updates regarding your order.

Thank you for your patience.

Customer Service, Copperline Coffee Machines
Marked for a person: yes
    [tokens] orchestrator: 3327 in, 512 out (call 3; running total 6993 in, 802 out)

Orchestrator: All drafts are complete and approved. Now I'll finish the job with a summary for the purchasing manager.

### CHOICE 5/10: finish {"summary": "Danforth Pump Works notified of a partial shipment issue on PO-5512 (200 PU-20 pumps): only 40 units will arrive on schedule, with the remaining 160 delayed 15 days. This affects 3 customer orders (55 units total): O-2203 (Elm State University Dining, 10 short, late penalty, 7-day delay), O-2204 (Kettle & Crumb Bakery, 20 short, no penalty, delay within tolerance), and O-2205 (Tall Pines Coffee House, 25 short, late penalty, 2-day delay).\n\nDrafted and saved for review:\n1. Reply to supplier confirming the revised schedule, declining to share customer details (per policy), and asking if any expedited/partial early shipment is possible to reduce delay.\n2. Customer delay notices for the two penalty-exposed orders (O-2203 and O-2205).\n\nA person should decide: (1) whether to send these drafts as-is, (2) whether to also notify Kettle & Crumb Bakery (O-2204, no penalty but still short), (3) whether to re-sequence the day-19 shipment to prioritize the penalty orders, and (4) whether to pursue an expedited partial shipment from Danforth.", "decision_needed": true}

============================================================
ORCHESTRATOR'S FINISH
============================================================

Decision needed by a person: YES

Danforth Pump Works notified of a partial shipment issue on PO-5512 (200 PU-20 pumps): only 40 units will arrive on schedule, with the remaining 160 delayed 15 days. This affects 3 customer orders (55 units total): O-2203 (Elm State University Dining, 10 short, late penalty, 7-day delay), O-2204 (Kettle & Crumb Bakery, 20 short, no penalty, delay within tolerance), and O-2205 (Tall Pines Coffee House, 25 short, late penalty, 2-day delay).

Drafted and saved for review:
1. Reply to supplier confirming the revised schedule, declining to share customer details (per policy), and asking if any expedited/partial early shipment is possible to reduce delay.
2. Customer delay notices for the two penalty-exposed orders (O-2203 and O-2205).

A person should decide: (1) whether to send these drafts as-is, (2) whether to also notify Kettle & Crumb Bakery (O-2204, no penalty but still short), (3) whether to re-sequence the day-19 shipment to prioritize the penalty orders, and (4) whether to pursue an expedited partial shipment from Danforth.

Tool calls used: 5/10

============================================================
SUMMARY
============================================================

Drafts saved: 3
  supplier PU-20   APPROVE 0 revision(s)  week-05/drafts/copperline-coffee/20261123-212704-supplier-PU-20.md
  customer O-2203  APPROVE 0 revision(s)  week-05/drafts/copperline-coffee/20261123-212710-customer-O-2203.md
  customer O-2205  APPROVE 0 revision(s)  week-05/drafts/copperline-coffee/20261123-212716-customer-O-2205.md

Drafts marked for a person: 3
  supplier PU-20: order(s) with a late penalty: O-2203, O-2205
  customer O-2203: order O-2203 has a late penalty
  customer O-2205: order O-2205 has a late penalty

Decisions for a person: 4
  - Decide whether to notify Elm State University Dining and Tall Pines Coffee House (both have late penalties) about the expected 2-7 day delay.
  - Decide whether to re-sequence shipments to prioritize late-penalty orders (O-2203, O-2205) ahead of O-2204 once the day-19 shipment arrives.
  - Decide whether to seek an expedited partial shipment from Danforth to cover the shortfall before day 19.
  - Decide what information to send Danforth in response to their request (customer names and quantities for PO-5512).

Tokens used by each specialist
  specialist       calls     input    output
  orchestrator         3      6993       802
  analyst              3     11411       923
  supplier_writer      1      1146       296
  reviewer             3      2708      1605
  customer_writer      2       729       324
  total                      22987      3950
```

## Reflection
Email 1 reading: Matches, because Diane says the other 160 pumps will follow 15 days after the scheduled date, and PO-5512 was scheduled for day 4, so the brief's what_changed line correctly gives day 19 for the 160 pumps.

Email 1 customer names: No, the reply to Diane named no customer; it said customer names and order details are confidential and only told her that 55 units across three orders are affected.

Email 4 orchestrator: None, because the orchestrator went straight to finish since Pelham said PO-5540 is not affected; the pipeline still ran the analyst (three tool calls, no orders at risk), skipped the customer notices, and had the supplier writer return "NO REPLY NEEDED", so it saved no drafts but used about 10,500 input tokens compared with the orchestrator's 1,450.
