"""A company folder and its CSV files, with column names worked out from each header row.

A company folder (such as week-05/brookfield-bikes or week-05/copperline-coffee) holds:

    parts.csv  inbound.csv  orders.csv  bill-of-materials.csv  suppliers.csv
    prompts/   analyst.txt  customer-writer.txt  orchestrator.txt  reviewer.txt  supplier-writer.txt
    supplier-emails/        (the .eml files to run; optional)

Companies name their columns differently (Brookfield's orders.csv has model, qty,
ship_in_days; Copperline's has product, units, ship_by_day), so no column name is assumed.
For each file, every column is scored against the words a header for each field would use
("ship" and "day" for the ship day, "per" for parts per unit, ...) and checked against its
values (a quantity must be whole numbers, the product must be text). The product column of
orders.csv and bill-of-materials.csv is confirmed by matching their values against each
other. The rows come back with fixed field names, listed in FIELDS.

Used by supply_mcp_server.py and specialists.py.
"""

import csv
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ORG = HERE / "brookfield-bikes"
CSV_FILES = ("parts.csv", "inbound.csv", "orders.csv", "bill-of-materials.csv", "suppliers.csv")
PROMPTS = ("analyst", "customer-writer", "orchestrator", "reviewer", "supplier-writer")

# Field -> (kind of value, {header word: weight}, header words that rule a column out).
# kind: "int" (whole numbers), "text" (not all numbers), "flag" (yes/no), "any".
ROLES = {
    "part_id": ("any", {"part": 3, "component": 3, "sku": 2, "id": 1, "no": 1, "number": 1, "num": 1},
                {"per", "qty", "quantity", "description", "desc", "name", "supplier", "vendor"}),
    "product": ("text", {"model": 3, "product": 3, "item": 2, "machine": 2, "bike": 2, "sku": 1,
                         "finished": 1, "goods": 1},
                {"part", "component", "per", "qty", "quantity", "units", "count", "customer"}),
    "quantity": ("int", {"qty": 3, "quantity": 3, "units": 3, "unit": 2, "count": 2, "pieces": 2,
                         "pcs": 2, "amount": 1, "ordered": 1, "bikes": 2, "machines": 2},
                 {"per", "day", "days", "date", "short", "price", "cost", "penalty", "hand"}),
    "parts_per_unit": ("int", {"per": 4, "qty": 1, "quantity": 1, "count": 1, "each": 2,
                               "usage": 2, "unit": 1, "units": 1, "used": 1},
                       {"id", "day", "days"}),
    "ship_day": ("int", {"ship": 4, "shipping": 4, "ships": 4, "due": 3, "deliver": 2,
                         "delivery": 2, "promised": 2, "day": 1, "days": 1, "by": 0},
                 {"late", "penalty", "qty", "quantity", "units"}),
    "late_penalty": ("flag", {"penalty": 4, "late": 2, "liquidated": 2, "damages": 1, "fee": 1},
                     set()),
    "order_id": ("any", {"order": 3, "id": 1, "no": 1, "number": 1, "num": 1, "ref": 1},
                 {"qty", "quantity", "units", "email", "customer", "date", "day", "days"}),
    "customer": ("text", {"customer": 4, "client": 4, "account": 2, "buyer": 2, "company": 1,
                          "name": 1},
                 {"email", "contact", "id", "phone", "address"}),
    "description": ("text", {"description": 4, "desc": 4, "name": 2, "title": 1}, {"supplier"}),
    "on_hand": ("int", {"hand": 3, "onhand": 4, "stock": 3, "inventory": 3, "available": 2,
                        "qty": 1, "quantity": 1},
                {"day", "days", "per"}),
    "supplier": ("text", {"supplier": 4, "vendor": 4, "manufacturer": 3, "maker": 2},
                 {"contact", "email", "id", "phone"}),
    "po_number": ("any", {"po": 4, "purchase": 3, "order": 1, "number": 1, "no": 1},
                  {"qty", "quantity", "day", "days"}),
    "arrives_day": ("int", {"arrive": 4, "arrives": 4, "arrival": 4, "arriving": 4, "eta": 4,
                            "due": 2, "delivery": 2, "day": 1, "days": 1},
                    {"qty", "quantity"}),
    "contact_name": ("text", {"contact": 3, "name": 2, "rep": 2, "person": 2},
                     {"email", "phone", "id"}),
}

# The fields worked out for each file, in the order they are assigned.
FIELDS = {
    "parts.csv": ("part_id", "on_hand", "description", "supplier"),
    "inbound.csv": ("po_number", "part_id", "arrives_day", "quantity", "supplier"),
    "orders.csv": ("order_id", "customer", "product", "late_penalty", "ship_day", "quantity"),
    "bill-of-materials.csv": ("part_id", "product", "parts_per_unit"),
    "suppliers.csv": ("supplier", "contact_name"),
}

YES = {"yes", "y", "true", "t", "1"}
NO = {"no", "n", "false", "f", "0", ""}


class OrgError(Exception):
    pass


# ---------------------------------------------------------------------------
# The company folder
# ---------------------------------------------------------------------------

def resolve_org(value=None):
    """The company folder for --org: a path, or a folder name inside week-05.
    None gives week-05/brookfield-bikes. Raises OrgError if it isn't a complete folder."""
    if not value:
        folder = DEFAULT_ORG
    else:
        given = Path(value).expanduser()
        folder = given if given.is_dir() else HERE / value
    folder = folder.resolve()
    if not folder.is_dir():
        raise OrgError(f"No company folder '{value}' (looked for {given} and {HERE / value}).")
    missing = [f for f in CSV_FILES if not (folder / f).is_file()]
    missing += [f"prompts/{p}.txt" for p in PROMPTS if not (folder / "prompts" / f"{p}.txt").is_file()]
    if missing:
        inner = [d.name for d in folder.iterdir() if d.is_dir() and (d / "orders.csv").is_file()]
        hint = f" It holds a folder '{inner[0]}' that looks like the real one." if inner else ""
        raise OrgError(f"{folder} is missing: {', '.join(missing)}.{hint}")
    return folder


def add_org_argument(parser):
    parser.add_argument(
        "--org", metavar="FOLDER",
        help="company folder with the five CSV files, prompts/ and supplier-emails/: a path, "
             "or a folder name in week-05 such as copperline-coffee (default: brookfield-bikes)")


# ---------------------------------------------------------------------------
# Column detection
# ---------------------------------------------------------------------------

def _words(header):
    return [w for w in re.split(r"[^a-z0-9]+", header.lower()) if w]


def to_int(value):
    return int(float(str(value).replace(",", "").strip()))


def _is_int(value):
    try:
        return float(str(value).replace(",", "").strip()).is_integer()
    except ValueError:
        return False


def _fits(kind, values):
    values = [v for v in values if v != ""]
    if kind == "int":
        return bool(values) and all(_is_int(v) for v in values)
    if kind == "text":
        return not values or not all(_is_int(v) for v in values)
    if kind == "flag":
        return all(v.lower() in YES | NO for v in values)
    return True


def _score(role, header):
    _, weights, exclude = ROLES[role]
    words = _words(header)
    if any(w in exclude for w in words):
        return 0
    return sum(weights.get(w, 0) for w in words)


def detect_columns(name, headers, rows, fields=None):
    """{field: header} for one file. Each header is used at most once. Raises OrgError if a
    field matches no column, or two columns equally well."""
    fields = fields or FIELDS[name]
    free = list(headers)
    found = {}
    for field in fields:
        kind = ROLES[field][0]
        scored = [(_score(field, h), h) for h in free if _fits(kind, [r[h] for r in rows])]
        scored = sorted((s for s in scored if s[0] > 0), reverse=True)
        if not scored:
            raise OrgError(f"{name}: no column looks like {field.replace('_', ' ')} "
                           f"(headers: {', '.join(headers)}).")
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            raise OrgError(f"{name}: can't tell which column is {field.replace('_', ' ')}: "
                           f"'{scored[0][1]}' or '{scored[1][1]}'.")
        found[field] = scored[0][1]
        free.remove(scored[0][1])
    return found


def _read(path):
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = [h.strip() for h in (reader.fieldnames or [])]
        rows = [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]
    return headers, rows


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

class OrgData:
    """All five CSV files of a company folder, with fixed field names:

    parts[part_id]  -> {part_id, description, on_hand (int), supplier}
    inbound         -> [{po_number, part_id, supplier, quantity (int), arrives_day (int)}]
    orders          -> [{order_id, customer, product, quantity (int), ship_day (int), late_penalty (bool)}]
    bom             -> [{product, part_id, parts_per_unit (int)}]
    suppliers       -> [{supplier, contact_name}]
    columns         -> {file: {field: header in that file}}
    """

    def __init__(self, folder):
        self.dir = Path(folder)
        raw = {name: _read(self.dir / name) for name in CSV_FILES}
        self.columns = {name: detect_columns(name, *raw[name]) for name in CSV_FILES}
        self._check_products(raw)

        def rows(name):
            cols = self.columns[name]
            return [{field: r[h] for field, h in cols.items()} for r in raw[name][1]]

        self.parts = {}
        for r in rows("parts.csv"):
            r["part_id"] = r["part_id"].upper()
            r["on_hand"] = to_int(r["on_hand"])
            self.parts[r["part_id"]] = r
        self.inbound = [dict(r, part_id=r["part_id"].upper(), quantity=to_int(r["quantity"]),
                             arrives_day=to_int(r["arrives_day"])) for r in rows("inbound.csv")]
        self.orders = [dict(r, quantity=to_int(r["quantity"]), ship_day=to_int(r["ship_day"]),
                            late_penalty=r["late_penalty"].lower() in YES)
                       for r in rows("orders.csv")]
        self.bom = [dict(r, part_id=r["part_id"].upper(), parts_per_unit=to_int(r["parts_per_unit"]))
                    for r in rows("bill-of-materials.csv")]
        self.suppliers = rows("suppliers.csv")

    def _check_products(self, raw):
        """The product column of orders.csv must hold products named in the bill of
        materials. If the header guess fails that, use the text column whose values do."""
        def values(name, h):
            return {r[h] for r in raw[name][1] if r[h]}

        bom_h = self.columns["bill-of-materials.csv"]["product"]
        ord_h = self.columns["orders.csv"]["product"]
        bom_products = values("bill-of-materials.csv", bom_h)
        if values("orders.csv", ord_h) <= bom_products:
            return
        used = set(self.columns["orders.csv"].values()) - {ord_h}
        for h in raw["orders.csv"][0]:
            if h not in used and values("orders.csv", h) and values("orders.csv", h) <= bom_products:
                # Swap: the matching column is the product; give its old field the guessed one.
                for field, col in self.columns["orders.csv"].items():
                    if col == h:
                        self.columns["orders.csv"][field] = ord_h
                self.columns["orders.csv"]["product"] = h
                return
        raise OrgError(f"orders.csv: no column holds the products named in bill-of-materials.csv "
                       f"column '{bom_h}'.")

    def describe_columns(self):
        """Lines naming the column found for each key field, for the log."""
        keys = {"orders.csv": ("product", "quantity", "ship_day"),
                "bill-of-materials.csv": ("product", "parts_per_unit")}
        return [f"{name}: " + ", ".join(f"{k} = '{self.columns[name][k]}'" for k in fields)
                for name, fields in keys.items()]

    def part(self, part_id):
        """The part row (case-insensitive), or None."""
        return self.parts.get((part_id or "").strip().upper())

    def products_using(self, part_id):
        """{product: parts per unit} for every product whose bill of materials has the part."""
        return {r["product"]: r["parts_per_unit"] for r in self.bom if r["part_id"] == part_id}

    def orders_needing(self, part_id):
        """Open orders that need the part, in ship-day order (ties by order_id)."""
        per_unit = self.products_using(part_id)
        rows = [{
            "order_id": o["order_id"],
            "customer": o["customer"],
            "product": o["product"],
            "quantity": o["quantity"],
            "parts_per_unit": per_unit[o["product"]],
            "units_needed": o["quantity"] * per_unit[o["product"]],
            "ship_day": o["ship_day"],
            "late_penalty": o["late_penalty"],
        } for o in self.orders if o["product"] in per_unit]
        return sorted(rows, key=lambda r: (r["ship_day"], r["order_id"]))

    def order(self, order_id):
        return next((o for o in self.orders if o["order_id"].upper() == order_id.upper()), None)

    def customer_names(self):
        return sorted({o["customer"] for o in self.orders if o["customer"]}, key=len, reverse=True)

    def supplier_names(self):
        names = {s for r in self.suppliers for s in (r["supplier"], r["contact_name"]) if s}
        names |= {p["supplier"] for p in self.parts.values() if p["supplier"]}
        return sorted(names, key=len, reverse=True)
