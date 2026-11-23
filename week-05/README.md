# Week 05

Materials for week 5 of GBUS 562: AI Language Models.

This week's exercises build a team of AI agents with Claude Code that prepares Brookfield Bicycle Company's response to one supplier email: a supply analyst that uses data tools, a writer for supplier replies, a writer for customer notices, and a reviewer.

## Contents

- [supply_mcp_server.py](supply_mcp_server.py): an MCP server (stdio) that reads the CSV files in `brookfield-bikes/`, with three read-only tools:
  - `get_part(part_id)`: the part's stock on hand, its scheduled shipments, and the models that use it.
  - `get_orders_for_part(part_id)`: the open orders that need the part, with units needed (bikes times parts per bike), ship day, and late penalty.
  - `check_coverage(part_id, arrivals)`: takes a new list of arrivals (quantity and day), which replaces the part's scheduled shipments. Orders are filled complete, in ship-day order, so a short order holds up the ones after it. Each order is returned as covered, or with units short and the first day it can ship.
  - Days are counted from today (day 0).
- [brookfield-bikes](brookfield-bikes/): Brookfield's data:
  - five CSV files: `parts.csv`, `inbound.csv`, `orders.csv`, `bill-of-materials.csv`, `suppliers.csv`
  - `supplier-emails/`: three test emails
  - `prompts/`: the instructions for the analyst, the two writers, the reviewer, and the orchestrator
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

The paths are relative, so start Claude Code from the top of the repository when you use the server. To point the server at another data folder, add `--data <folder>` at the end of the command.
