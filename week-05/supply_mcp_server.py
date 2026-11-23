"""MCP server with one company's supply data: parts, orders, and coverage.

Tools (all read-only):
  get_part(part_id)                  stock on hand, scheduled shipments, products that use it
  get_orders_for_part(part_id)       open orders that need the part, with units needed
  check_coverage(part_id, arrivals)  which orders a new arrival schedule covers, and
                                     for the rest, units short and the first day they can ship

Usage (from the top of the repository):
    .venv/bin/python week-05/supply_mcp_server.py
    .venv/bin/python week-05/supply_mcp_server.py --org copperline-coffee
    .venv/bin/python week-05/supply_mcp_server.py --org week-05/copperline-coffee

--org names a company folder with parts.csv, inbound.csv, orders.csv, bill-of-materials.csv,
suppliers.csv and prompts/ (see org_data.py): a path, or a folder name inside week-05.
Without it, week-05/brookfield-bikes is used. (--data is an older name for --org.)
Column names are worked out from each file's header row, so they may differ by company.
The CSV files are read again on every tool call, so edits take effect at once.

Days are counted from today: day 0 is today. Stock on hand is available on day 0, and a
shipment arriving on day N can be used for orders shipping on day N or later.

The server talks MCP over stdio, so stdout belongs to the protocol: every message from
this file goes to stderr.
"""

import argparse
import sys
from typing import Annotated

from pydantic import BaseModel, Field

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from org_data import OrgData, OrgError, resolve_org


def log(*args):
    print("[supply_mcp_server]", *args, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load(folder):
    """The company folder's CSV files, read fresh on every tool call."""
    try:
        return OrgData(folder)
    except OrgError as e:
        raise ToolError(str(e))


def find_part(data, part_id):
    """Return the part row for part_id (case-insensitive), or raise ToolError."""
    part = data.part(part_id)
    if part is None:
        known = ", ".join(sorted(data.parts))
        raise ToolError(f"Unknown part_id '{part_id}'. Known parts: {known}.")
    return part


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

def coverage(on_hand, arrivals, orders):
    """Fill orders complete, in ship-day order, from stock on hand plus the arrivals.

    Orders are filled one after another, so a short order holds up every order after it:
    order k can ship on the first day when everything that has arrived covers orders
    1..k in full. An order is covered if that day is on or before its ship day.

    arrivals is a list of (qty, day). Returns one dict per order.
    """
    events = sorted(arrivals, key=lambda a: a[1])

    def supply_by(day):
        return on_hand + sum(q for q, d in events if d <= day)

    def first_day_with(units):
        if on_hand >= units:
            return 0
        total = on_hand
        for qty, day in events:
            total += qty
            if total >= units:
                return day
        return None  # the arrivals never cover it

    results, needed_so_far = [], 0
    for o in orders:
        needed_so_far += o["units_needed"]
        ready_day = first_day_with(needed_so_far)
        row = {k: o[k] for k in ("order_id", "customer", "product", "quantity",
                                 "units_needed", "ship_day", "late_penalty")}
        if ready_day is not None and ready_day <= o["ship_day"]:
            row["status"] = "covered"
        else:
            # Units still missing for this order on its ship day, after earlier orders.
            short = min(o["units_needed"], needed_so_far - supply_by(o["ship_day"]))
            row["status"] = "short"
            row["units_short"] = short
            row["quantity_short"] = -(-short // o["parts_per_unit"])  # ceiling
            row["first_ship_day"] = ready_day
            row["days_late"] = None if ready_day is None else ready_day - o["ship_day"]
        results.append(row)
    return results


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

class Arrival(BaseModel):
    qty: int = Field(gt=0, description="Number of parts (not finished products) in this arrival.")
    day: int = Field(ge=0, description="Arrival day, counted from today (0 = today).")


PartId = Annotated[str, Field(description="Part number, such as MC-500.")]


def create_server(data_dir):
    mcp = MCPServer(f"{data_dir.name}-supply")
    read_only = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)

    def get_part(part_id: PartId) -> dict:
        data = load(data_dir)
        part = find_part(data, part_id)
        pid = part["part_id"]
        shipments = [
            {"po_number": r["po_number"], "supplier": r["supplier"], "qty": r["quantity"],
             "arrives_day": r["arrives_day"]}
            for r in data.inbound if r["part_id"] == pid
        ]
        return {
            "part_id": pid,
            "description": part["description"],
            "supplier": part["supplier"],
            "on_hand": part["on_hand"],
            "scheduled_shipments": sorted(shipments, key=lambda s: s["arrives_day"]),
            "used_in_products": [{"product": m, "parts_per_unit": n}
                                 for m, n in data.products_using(pid).items()],
            "days": "counted from today (day 0)",
        }

    def get_orders_for_part(part_id: PartId) -> dict:
        data = load(data_dir)
        pid = find_part(data, part_id)["part_id"]
        orders = [{k: v for k, v in o.items() if k != "parts_per_unit"}
                  for o in data.orders_needing(pid)]
        return {
            "part_id": pid,
            "orders": orders,
            "total_units_needed": sum(o["units_needed"] for o in orders),
            "units": "quantity is finished products ordered; units_needed is parts: "
                     "quantity times parts per unit",
            "days": "ship_day is counted from today (day 0)",
        }

    def check_coverage(part_id: PartId, arrivals: list[Arrival]) -> dict:
        data = load(data_dir)
        part = find_part(data, part_id)
        pid, on_hand = part["part_id"], part["on_hand"]
        results = coverage(on_hand, [(a.qty, a.day) for a in arrivals], data.orders_needing(pid))
        short = [r for r in results if r["status"] == "short"]
        return {
            "part_id": pid,
            "on_hand": on_hand,
            "arrivals_used": [{"qty": a.qty, "day": a.day}
                              for a in sorted(arrivals, key=lambda a: a.day)],
            "orders": results,
            "orders_short": [r["order_id"] for r in short],
            "rules": ("Arrivals replace the part's scheduled shipments. Orders are filled "
                      "complete in ship-day order, so a short order holds up later ones. "
                      "units_short is parts; first_ship_day null means these arrivals never "
                      "cover the order."),
        }

    mcp.add_tool(
        get_part, annotations=read_only,
        description=(
            "Get one part's stock on hand, its scheduled shipments (purchase order, supplier, "
            "quantity, arrival day), and the products that use it with parts per unit. "
            "Days are counted from today (day 0)."
        ),
    )
    mcp.add_tool(
        get_orders_for_part, annotations=read_only,
        description=(
            "List the open customer orders that need a part, in ship-day order: order id, "
            "customer, product, quantity (products ordered), units_needed (quantity times parts "
            "per unit), ship_day "
            "(days from today), and late_penalty (true or false)."
        ),
    )
    mcp.add_tool(
        check_coverage, annotations=read_only,
        description=(
            "Check which open orders a part's arrival schedule covers. arrivals is the full "
            "new list of shipments for the part (quantity in parts, day from today); it "
            "REPLACES the part's scheduled shipments, so include every arrival you expect. "
            "Stock on hand counts from day 0. Orders are filled complete in ship-day order, "
            "so a short order holds up the ones after it. For each order returns status "
            "'covered', or 'short' with units_short (parts), quantity_short (products), "
            "first_ship_day, and "
            "days_late. To compare a supplier's options, call it once per option."
        ),
    )
    return mcp


def main():
    parser = argparse.ArgumentParser(description="Supply data MCP server (stdio) for one company folder.")
    parser.add_argument("--org", "--data", dest="org", metavar="FOLDER",
                        help="company folder: a path, or a folder name in week-05 "
                             "(default: brookfield-bikes)")
    args = parser.parse_args()

    try:
        data_dir = resolve_org(args.org)
        data = OrgData(data_dir)  # fail now, not on the first tool call, if a file can't be read
    except OrgError as e:
        sys.exit(str(e))
    log(f"Company folder: {data_dir}")
    for line in data.describe_columns():
        log(f"  {line}")
    create_server(data_dir).run("stdio")


if __name__ == "__main__":
    main()
