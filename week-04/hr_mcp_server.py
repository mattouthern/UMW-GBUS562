"""MCP server for an organization's HR team: policy search and leave balance lookup.

Tools:
  search_policy(query)       the 4 closest chunks of the HR documents, with source files
  get_leave_balance(email)   one employee's leave balances from employees.csv
  get_leave_balance()        with --as-employee: only that employee's balances

Usage (from the top of the repository):
    .venv/bin/python week-04/hr_mcp_server.py                        # HR staff mode
    .venv/bin/python week-04/hr_mcp_server.py --as-employee megan.turner@hartwellfoods.example
    .venv/bin/python week-04/hr_mcp_server.py --rebuild              # rebuild the database and exit
    .venv/bin/python week-04/hr_mcp_server.py --org week-04/ridgeline-hotels --rebuild

--org names an organization folder laid out like week-04/hartwell-hr:
    hr-docs/            the HR policy documents (.html)
    employees.csv       one row per employee; the email and balance columns are
                        worked out from the header row, so their names can differ
    system_prompt.txt   the agent's system prompt (read by agent.py)
    sample-emails/      optional example emails
Without --org, week-04/hartwell-hr is used.

The server talks MCP over stdio, so stdout belongs to the protocol: every
message from this file goes to stderr.

The search is adapted from week-03/ask.py: one chunk per document section, each
starting with "<title> - <section>", stored in Chroma with its built-in
embedding model. Each organization has its own database in
week-04/chroma_db/<organization folder name>/ (ignored by Git), rebuilt
automatically when its documents change.
"""

import argparse
import contextlib
import csv
import hashlib
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ORG_DIR = HERE / "hartwell-hr"
DB_ROOT = HERE / "chroma_db"  # ignored by Git; one subfolder per organization

COLLECTION = "hr_docs"
TOP_K = 4
MAX_CHUNK_CHARS = 1500  # longer sections are split at paragraph boundaries


def log(*args):
    print(*args, file=sys.stderr)


# ---------------------------------------------------------------------------
# Loading and chunking the HR documents (from week-03/ask.py)
# ---------------------------------------------------------------------------

def clean(text):
    return re.sub(r"\s+", " ", text).strip()


def load_html(path):
    """Return (title, meta, [(heading, body), ...]) for one HTML page."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    root = soup.find("main") or soup.body or soup
    for tag in root.find_all(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    h1 = root.find("h1")
    title = clean(h1.get_text()) if h1 else (clean(soup.title.get_text()) if soup.title else path.stem)
    title = title.split("|")[0].strip()
    meta_tag = root.find(class_="meta")
    meta = clean(meta_tag.get_text()) if meta_tag else ""

    def block_text(el):
        if el.name in ("ul", "ol"):
            return "\n".join("- " + clean(li.get_text()) for li in el.find_all("li"))
        return clean(el.get_text())

    sections, heading, parts = [], "Introduction", []
    for el in root.find_all(["h2", "h3", "p", "ul", "ol", "table"]):
        if el.find_parent(["ul", "ol", "table"]) or el is meta_tag:
            continue
        if el.name in ("h2", "h3"):
            if parts:
                sections.append((heading, "\n".join(parts)))
            heading, parts = clean(el.get_text()), []
        elif el.name == "table":
            rows = [" | ".join(clean(c.get_text()) for c in tr.find_all(["th", "td"]))
                    for tr in el.find_all("tr")]
            parts.append("\n".join(rows))
        elif (t := block_text(el)):
            parts.append(t)
    if parts:
        sections.append((heading, "\n".join(parts)))
    return title, meta, sections


def split_long(body):
    """Split a long section at paragraph boundaries into pieces under MAX_CHUNK_CHARS."""
    if len(body) <= MAX_CHUNK_CHARS:
        return [body]
    pieces, current = [], ""
    for para in body.split("\n"):
        if current and len(current) + len(para) + 1 > MAX_CHUNK_CHARS:
            pieces.append(current)
            current = para
        else:
            current = f"{current}\n{para}" if current else para
    if current:
        pieces.append(current)
    return pieces


def doc_files(docs_dir):
    return sorted(p for p in docs_dir.rglob("*")
                  if p.is_file() and p.suffix.lower() in (".html", ".htm"))


def make_chunks(docs_dir):
    chunks = []
    for path in doc_files(docs_dir):
        source = path.relative_to(docs_dir).as_posix()
        try:
            title, meta, sections = load_html(path)
        except Exception as e:
            log(f"  Skipping {source}: could not read it ({e})")
            continue
        count = 0
        for heading, body in sections:
            pieces = split_long(body)
            for n, piece in enumerate(pieces, 1):
                label = heading if len(pieces) == 1 else f"{heading} (part {n} of {len(pieces)})"
                header = f"{title} - {label}"
                text = f"{header}\n{meta}\n\n{piece}" if meta else f"{header}\n\n{piece}"
                chunks.append({
                    "id": f"{source}::{count}",
                    "text": text,
                    "meta": {"source": source, "title": title, "section": label,
                             "version": meta, "chunk": count},
                })
                count += 1
        log(f"  {source}: {count} chunks")
    return chunks


def fingerprint(docs_dir):
    """Changes whenever a document is added, removed, or edited, or the folder moves."""
    h = hashlib.sha256(str(docs_dir.resolve()).encode())
    for p in doc_files(docs_dir):
        st = p.stat()
        h.update(f"{p.relative_to(docs_dir)}|{st.st_size}|{int(st.st_mtime)}".encode())
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Chroma database
# ---------------------------------------------------------------------------

def chroma_client(db_dir):
    # Chroma needs SQLite 3.35+; this codespace's built-in SQLite is older.
    try:
        import pysqlite3
        sys.modules["sqlite3"] = pysqlite3
    except ImportError:
        pass
    import chromadb
    from chromadb.config import Settings

    db_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(db_dir), settings=Settings(anonymized_telemetry=False))


def embedder():
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    return DefaultEmbeddingFunction()  # Chroma's built-in model (all-MiniLM-L6-v2)


def build_index(client, org):
    log(f"Indexing {org.docs_dir} ...")
    chunks = make_chunks(org.docs_dir)
    if not chunks:
        sys.exit(f"No HTML documents found in {org.docs_dir}")
    if COLLECTION in [c.name if hasattr(c, "name") else c for c in client.list_collections()]:
        client.delete_collection(COLLECTION)
    col = client.create_collection(
        COLLECTION, embedding_function=embedder(),
        metadata={"hnsw:space": "cosine", "fingerprint": fingerprint(org.docs_dir)},
    )
    col.add(ids=[c["id"] for c in chunks], documents=[c["text"] for c in chunks],
            metadatas=[c["meta"] for c in chunks])
    log(f"Done: {len(chunks)} chunks stored in {org.db_dir}")
    return col


def open_index(org, rebuild=False):
    """Open the org's database, building it if it's missing, out of date, or --rebuild was given."""
    client = chroma_client(org.db_dir)
    if rebuild:
        return build_index(client, org)
    try:
        col = client.get_collection(COLLECTION, embedding_function=embedder())
    except Exception:
        log("No database yet, building it now.")
        return build_index(client, org)
    if (col.metadata or {}).get("fingerprint") != fingerprint(org.docs_dir):
        log("The HR documents have changed since the database was built; rebuilding.")
        return build_index(client, org)
    return col


# ---------------------------------------------------------------------------
# Organization folder
# ---------------------------------------------------------------------------

def resolve_org_dir(arg=None):
    """Path of the organization folder: --org as given, or a folder of that name
    beside this file, or week-04/hartwell-hr when --org is not used."""
    if not arg:
        return DEFAULT_ORG_DIR
    path = Path(arg).expanduser()
    if not path.exists() and (HERE / arg).exists():
        path = HERE / arg
    return path.resolve()


class Org:
    """One organization's files: HR documents, employee file, system prompt, database."""

    def __init__(self, arg=None):
        self.dir = resolve_org_dir(arg)
        self.name = self.dir.name
        self.is_default = self.dir == DEFAULT_ORG_DIR.resolve()
        self.docs_dir = self.dir / "hr-docs"
        self.employees_csv = self.dir / "employees.csv"
        self.system_prompt_file = self.dir / "system_prompt.txt"
        self.db_dir = DB_ROOT / self.name

        if not self.dir.is_dir():
            sys.exit(f"Organization folder not found: {arg or self.dir}")
        missing = [p.name + ("/" if p is self.docs_dir else "")
                   for p in (self.docs_dir, self.employees_csv, self.system_prompt_file)
                   if not p.exists()]
        if missing:
            sys.exit(f"The organization folder {self.dir} is missing: {', '.join(missing)}. "
                     "It needs hr-docs/, employees.csv and system_prompt.txt directly inside it.")
        self.columns, _ = self.read_employees()  # fail now if the columns can't be worked out

    def read_employees(self):
        """Return (columns, rows) of the employee file. Re-read on every call."""
        # utf-8-sig drops the byte-order mark that Excel puts at the start of CSV files.
        with self.employees_csv.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            headers = [h for h in (reader.fieldnames or []) if h and h.strip()]
        return detect_columns(headers, rows, self.employees_csv), rows

    def find_employee(self, email):
        """Return the employee's row from the employee file, or None."""
        wanted = normalize_email(email)
        cols, rows = self.read_employees()
        for row in rows:
            if normalize_email(row.get(cols["email"]) or "") == wanted:
                return row
        return None

    def describe_columns(self):
        c = self.columns
        return (f"email = '{c['email']}', name = '{c['name'] or '(none)'}', "
                f"balances = {', '.join(repr(b) for b in c['balances'])}")


# ---------------------------------------------------------------------------
# Employee records: the column names are worked out from the header row
# ---------------------------------------------------------------------------

EMAIL_VALUE = re.compile(r"^<?[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+>?$")
BALANCE_WORDS = {"left", "remaining", "remain", "balance", "balances", "available",
                 "accrued", "unused", "bank", "banked"}
UNIT_WORDS = {"days", "day", "hours", "hour", "hrs", "hr"}


def words(header):
    """'PTO Days Remaining' / 'vacation_days_left' -> {'pto', 'days', 'remaining'} etc."""
    return set(re.sub(r"[^a-z0-9]+", " ", header.lower()).split())


def to_number(value):
    """int or float for a numeric cell, otherwise None."""
    try:
        n = float(str(value).strip().replace(",", ""))
    except ValueError:
        return None
    return int(n) if n.is_integer() else n


def detect_columns(headers, rows, path):
    """Work out which columns hold the email address, the name and the leave balances.

    email:    the column whose filled cells all look like email addresses
              (a header mentioning "mail" wins a tie).
    balances: numeric columns whose header says it's a balance ("left", "remaining",
              "balance", "available", ...) or counts days or hours.
    name:     a header with the word "name" ("Full Name", "Employee Name", "name"), or
              "first name" + "last name" together.
    """
    def filled(h):
        return [(r.get(h) or "").strip() for r in rows if (r.get(h) or "").strip()]

    shown = ", ".join(repr(h) for h in headers) or "(no header row)"

    emails = [h for h in headers if filled(h) and all(EMAIL_VALUE.match(v) for v in filled(h))]
    emails.sort(key=lambda h: not ({"mail", "email", "e"} & words(h) or "mail" in h.lower()))
    if not emails:
        emails = [h for h in headers if "mail" in h.lower()]
    if not emails:
        sys.exit(f"Could not find an email address column in {path}. Header row: {shown}")
    email = emails[0]

    balances = [
        h for h in headers
        if h != email and filled(h)
        and all(to_number(v) is not None for v in filled(h))
        and (words(h) & BALANCE_WORDS or words(h) & UNIT_WORDS)
    ]
    if not balances:
        sys.exit(f"Could not find any leave balance columns in {path} (numeric columns "
                 f"named like 'days left' or 'hours remaining'). Header row: {shown}")

    named = [h for h in headers if "name" in words(h) and not words(h) & {"first", "last", "user"}]
    first = next((h for h in headers if {"first", "given"} & words(h)), None)
    last = next((h for h in headers if {"last", "family", "surname"} & words(h)), None)
    name = named[0] if named else None
    name_parts = [first, last] if not name and first and last else None

    return {"email": email, "name": name, "name_parts": name_parts, "balances": balances,
            "details": [h for h in headers
                        if h not in {email, name, *(name_parts or []), *balances}]}


def normalize_email(email):
    return email.strip().strip("<>").strip().lower()


def balance_or_error(org, email):
    from mcp.server.mcpserver.exceptions import ToolError

    row = org.find_employee(email) if email.strip() else None
    if row is None:
        raise ToolError(
            f"No employee record matches '{email}'. Do not state, estimate, or guess any "
            "leave balance. Say that HR will confirm the balance, and flag the reply "
            "for human review."
        )
    cols, _ = org.read_employees()
    if cols["name"]:
        name = (row.get(cols["name"]) or "").strip()
    elif cols["name_parts"]:
        name = " ".join((row.get(h) or "").strip() for h in cols["name_parts"]).strip()
    else:
        name = ""
    return {
        "email": normalize_email(row[cols["email"]]),
        "name": name or None,
        "balances": {h.strip(): to_number(row.get(h) or "") for h in cols["balances"]},
        "details": {h.strip(): (row.get(h) or "").strip() or None for h in cols["details"]},
    }


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

def document_topics(org):
    """'pto-policy.html' -> 'pto policy', for the search_policy description."""
    return ", ".join(p.stem.replace("-", " ").replace("_", " ") for p in doc_files(org.docs_dir))


def create_server(org, col, as_employee=None):
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    mcp = MCPServer(org.name)
    read_only = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
    balance_names = ", ".join(org.columns["balances"])

    def search_policy(query: str) -> dict:
        res = col.query(query_texts=[query], n_results=TOP_K,
                        include=["documents", "metadatas", "distances"])
        results = [
            {"source": meta["source"], "title": meta["title"], "section": meta["section"],
             "version": meta["version"], "score": round(1 - dist, 3), "text": text}
            for text, meta, dist in zip(res["documents"][0], res["metadatas"][0],
                                        res["distances"][0])
        ]
        return {"query": query, "results": results}

    mcp.add_tool(
        search_policy, annotations=read_only,
        description=(
            f"Search the organization's HR policy documents ({document_topics(org)}).\n\n"
            f"Returns the {TOP_K} most relevant document sections. Each result has the source "
            "file to cite, the document title, section, version, a similarity score (higher is "
            "closer), and the section text. Use only what the returned text says; if no result "
            "answers the question, say the policies don't cover it."
        ),
    )

    returns = (f"Returns the name, the balances ({balance_names}) and the other details in "
               "the employee file.")
    if as_employee:
        def get_leave_balance() -> dict:
            return balance_or_error(org, as_employee)

        mcp.add_tool(
            get_leave_balance, annotations=read_only,
            description=(
                "Get the leave balances of the employee whose email is being answered. "
                f"{returns} Takes no inputs: it can return only that one employee's "
                "balances, never anyone else's."
            ),
        )
    else:
        def get_leave_balance(email: str) -> dict:
            return balance_or_error(org, email)

        mcp.add_tool(
            get_leave_balance, annotations=read_only,
            description=(
                "Look up one employee's leave balances by their work email address (for HR "
                f"staff). {returns} If no record matches, returns an error: then do not state "
                "any balance."
            ),
        )

    return mcp


def main():
    parser = argparse.ArgumentParser(description="HR policy and leave balance MCP server (stdio).")
    parser.add_argument("--org", metavar="FOLDER",
                        help="organization folder with hr-docs/, employees.csv and "
                             "system_prompt.txt (default: week-04/hartwell-hr)")
    parser.add_argument("--as-employee", metavar="EMAIL",
                        help="limit get_leave_balance to this one employee; it then takes no inputs")
    parser.add_argument("--rebuild", action="store_true",
                        help="rebuild the policy database and exit")
    args = parser.parse_args()

    # Keep anything Chroma or the embedding model prints off stdout (the MCP channel).
    with contextlib.redirect_stdout(sys.stderr):
        org = Org(args.org)
        log(f"Organization: {org.dir} (database: {org.db_dir})")
        log(f"Employee file columns: {org.describe_columns()}")
        col = open_index(org, rebuild=args.rebuild)
        if args.rebuild:
            return
        col.query(query_texts=["warm up"], n_results=1)  # load the embedding model now
        if args.as_employee and not org.find_employee(args.as_employee):
            log(f"Warning: no employee record matches {args.as_employee}; "
                "get_leave_balance will return an error.")

    create_server(org, col, as_employee=args.as_employee).run("stdio")


if __name__ == "__main__":
    main()
