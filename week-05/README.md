# Week 05

Materials for week 5 of GBUS 562: AI Language Models.

This week's exercises build a team of AI agents with Claude Code that prepares a company's response to one supplier email (Brookfield Bicycle Company by default, or any company folder given with `--org`, such as Copperline Coffee Machines): a supply analyst that uses data tools, a writer for supplier replies, a writer for customer notices, and a reviewer.

## Contents

- [supply_mcp_server.py](supply_mcp_server.py): an MCP server (stdio) that reads the CSV files in a company folder (`brookfield-bikes/` unless `--org` names another), with three read-only tools:
  - `get_part(part_id)`: the part's stock on hand, its scheduled shipments, and the products that use it.
  - `get_orders_for_part(part_id)`: the open orders that need the part, with units needed (quantity times parts per unit), ship day, and late penalty.
  - `check_coverage(part_id, arrivals)`: takes a new list of arrivals (quantity and day), which replaces the part's scheduled shipments. Orders are filled complete, in ship-day order, so a short order holds up the ones after it. Each order is returned as covered, or with units short and the first day it can ship.
  - Days are counted from today (day 0).
- [brookfield-bikes](brookfield-bikes/): Brookfield's data:
  - five CSV files: `parts.csv`, `inbound.csv`, `orders.csv`, `bill-of-materials.csv`, `suppliers.csv`
  - `supplier-emails/`: three test emails
  - `prompts/`: the instructions for the analyst, the two writers, the reviewer, and the orchestrator
- [copperline-coffee](copperline-coffee/): Copperline Coffee Machines' data, laid out the same way (four test emails). Its CSV column names differ from Brookfield's.
- [org_data.py](org_data.py): reads a company folder. It works out from each CSV header row which columns hold the product, the quantity, the parts per unit, the ship day, and the other fields, so companies can name their columns differently.
- [specialists.py](specialists.py), [pipeline.py](pipeline.py), [orchestrator.py](orchestrator.py): the specialists, a fixed-order pipeline, and an orchestrator that lets Claude choose the specialists. All take `--org <folder>`, read the data and the system prompts from that folder, print the analyst's brief in full and each final draft once, and save drafts in `drafts/<company folder>/`.
- [requirements.txt](requirements.txt): the Python packages the tools need.

## How to run it

From the top of the repository, install the packages into the virtual environment once:

```
.venv/bin/pip install -r week-05/requirements.txt
```

Add the supply server to Claude Code (run from the top of the repository, using the `.venv` Python):

```
claude mcp add brookfield-supply -- .venv/bin/python week-05/supply_mcp_server.py
```

The paths are relative, so start Claude Code from the top of the repository when you use the server. To point the server at another company, add `--org <folder>` at the end of the command (a path, or a folder name in `week-05`).

Run a supplier email through the pipeline or the orchestrator:

```
.venv/bin/python week-05/pipeline.py --org copperline-coffee --email week-05/copperline-coffee/supplier-emails/01-danforth-pump-delay.eml --save week-05/runs/copperline-01-pipeline.txt
.venv/bin/python week-05/orchestrator.py --org copperline-coffee --email week-05/copperline-coffee/supplier-emails/01-danforth-pump-delay.eml --save week-05/runs/copperline-01-orchestrator.txt
```
