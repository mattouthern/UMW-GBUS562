"""MCP server with Brookfield Bicycle Company's supply data: parts, orders, and coverage.

Tools (all read-only):
  get_part(part_id)                  stock on hand, scheduled shipments, models that use it
  get_orders_for_part(part_id)       open orders that need the part, with units needed
  check_coverage(part_id, arrivals)  which orders a new arrival schedule covers, and
                                     for the rest, units short and the first day they can ship

Usage (from the top of the repository):
    .venv/bin/python week-05/supply_mcp_server.py
    .venv/bin/python week-05/supply_mcp_server.py --data week-05/brookfield-bikes

--data names a folder with parts.csv, inbound.csv, orders.csv, bill-of-materials.csv
(and suppliers.csv, which no tool returns). Without it, week-05/brookfield-bikes is used.
The CSV files are read again on every tool call, so edits take effect at once.

Days are counted from today: day 0 is today. Stock on hand is available on day 0, and a
shipment arriving on day N can be used for orders shipping on day N or later.

The server talks MCP over stdio, so stdout belongs to the protocol: every message from
this file goes to stderr.
"""

import argparse
import csv
import sys
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

HERE = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = HERE / "brookfield-bikes"
FILES = ("parts.csv", "inbound.csv", "orders.csv", "bill-of-materials.csv")


def log(*args):
    print("[supply_mcp_server]", *args, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def read_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [{k.strip(): (v or "").strip() for k, v in row.items()}
                for row in csv.DictReader(f)]


class Data:
    """The CSV files of one data folder, read fresh each time."""

    def __init__(self, folder):
        self.dir = folder
        self.parts = {r["part_id"]: r for r in read_csv(folder / "parts.csv")}
        self.inbound = read_csv(folder / "inbound.csv")
        self.orders = read_csv(folder / "orders.csv")
        self.bom = read_csv(folder / "bill-of-materials.csv")

    def part(self, part_id):
        """Return the part row for part_id (case-insensitive), or raise ToolError."""
        key = (part_id or "").strip().upper()
        if key not in self.parts:
            known = ", ".join(sorted(self.parts))
            raise ToolError(f"Unknown part_id '{part_id}'. Known parts: {known}.")
        return self.parts[key]

    def models_using(self, part_id):
        """{model: parts per bike} for every model whose bill of materials has the part."""
        return {r["model"]: int(r["qty_per_bike"]) for r in self.bom if r["part_id"] == part_id}

    def orders_needing(self, part_id):
        """Open orders that need the part, in ship-day order (ties by order_id)."""
        per_bike = self.models_using(part_id)
        rows = []
        for o in self.orders:
            if o["model"] not in per_bike:
                continue
            bikes = int(o["qty"])
            rows.append({
                "order_id": o["order_id"],
                "customer": o["customer"],
                "model": o["model"],
                "bikes": bikes,
                "parts_per_bike": per_bike[o["model"]],
                "units_needed": bikes * per_bike[o["model"]],
                "ship_day": int(o["ship_in_days"]),
                "late_penalty": o["late_penalty"].lower() == "yes",
            })
        return sorted(rows, key=lambda r: (r["ship_day"], r["order_id"]))


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
        row = {k: o[k] for k in ("order_id", "customer", "model", "bikes",
                                 "units_needed", "ship_day", "late_penalty")}
        if ready_day is not None and ready_day <= o["ship_day"]:
            row["status"] = "covered"
        else:
            # Units still missing for this order on its ship day, after earlier orders.
            short = min(o["units_needed"], needed_so_far - supply_by(o["ship_day"]))
            row["status"] = "short"
            row["units_short"] = short
            row["bikes_short"] = -(-short // o["parts_per_bike"])  # ceiling
            row["first_ship_day"] = ready_day
            row["days_late"] = None if ready_day is None else ready_day - o["ship_day"]
        results.append(row)
    return results


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

class Arrival(BaseModel):
    qty: int = Field(gt=0, description="Number of parts (not bikes) in this arrival.")
    day: int = Field(ge=0, description="Arrival day, counted from today (0 = today).")


PartId = Annotated[str, Field(description="Part number, such as MC-500.")]


def create_server(data_dir):
    mcp = MCPServer("brookfield-supply")
    read_only = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)

    def get_part(part_id: PartId) -> dict:
        data = Data(data_dir)
        part = data.part(part_id)
        pid = part["part_id"]
        shipments = [
            {"po_number": r["po_number"], "supplier": r["supplier"], "qty": int(r["qty"]),
             "arrives_day": int(r["arrives_in_days"])}
            for r in data.inbound if r["part_id"] == pid
        ]
        return {
            "part_id": pid,
            "description": part["description"],
            "supplier": part["supplier"],
            "on_hand": int(part["on_hand"]),
            "scheduled_shipments": sorted(shipments, key=lambda s: s["arrives_day"]),
            "used_in_models": [{"model": m, "parts_per_bike": n}
                               for m, n in data.models_using(pid).items()],
            "days": "counted from today (day 0)",
        }

    def get_orders_for_part(part_id: PartId) -> dict:
        data = Data(data_dir)
        pid = data.part(part_id)["part_id"]
        orders = [{k: v for k, v in o.items() if k != "parts_per_bike"}
                  for o in data.orders_needing(pid)]
        return {
            "part_id": pid,
            "orders": orders,
            "total_units_needed": sum(o["units_needed"] for o in orders),
            "units": "units_needed is parts: bikes times parts per bike",
            "days": "ship_day is counted from today (day 0)",
        }

    def check_coverage(part_id: PartId, arrivals: list[Arrival]) -> dict:
        data = Data(data_dir)
        part = data.part(part_id)
        pid, on_hand = part["part_id"], int(part["on_hand"])
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
            "quantity, arrival day), and the bike models that use it with parts per bike. "
            "Days are counted from today (day 0)."
        ),
    )
    mcp.add_tool(
        get_orders_for_part, annotations=read_only,
        description=(
            "List the open customer orders that need a part, in ship-day order: order id, "
            "customer, model, bikes, units_needed (bikes times parts per bike), ship_day "
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
            "'covered', or 'short' with units_short, bikes_short, first_ship_day, and "
            "days_late. To compare a supplier's options, call it once per option."
        ),
    )
    return mcp


def main():
    parser = argparse.ArgumentParser(description="Brookfield supply data MCP server (stdio).")
    parser.add_argument("--data", metavar="FOLDER",
                        help="folder with the CSV files (default: week-05/brookfield-bikes)")
    args = parser.parse_args()

    data_dir = Path(args.data).expanduser().resolve() if args.data else DEFAULT_DATA_DIR
    missing = [f for f in FILES if not (data_dir / f).is_file()]
    if missing:
        sys.exit(f"Missing in {data_dir}: {', '.join(missing)}")
    Data(data_dir)  # fail now, not on the first tool call, if a file can't be read
    log(f"Data folder: {data_dir}")
    create_server(data_dir).run("stdio")


if __name__ == "__main__":
    main()
