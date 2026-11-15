"""MCP server for Hartwell Foods HR: policy search and leave balance lookup.

Tools:
  search_policy(query)       the 4 closest chunks of the HR documents, with source files
  get_leave_balance(email)   one employee's leave balance from employees.csv
  get_leave_balance()        with --as-employee: only that employee's balance

Usage (from the top of the repository):
    .venv/bin/python week-04/hr_mcp_server.py                        # HR staff mode
    .venv/bin/python week-04/hr_mcp_server.py --as-employee megan.turner@hartwellfoods.example
    .venv/bin/python week-04/hr_mcp_server.py --rebuild              # rebuild the database and exit

The server talks MCP over stdio, so stdout belongs to the protocol: every
message from this file goes to stderr.

The search is adapted from week-03/ask.py: one chunk per document section, each
starting with "<title> - <section>", stored in Chroma with its built-in
embedding model. The database lives in week-04/chroma_db/ (ignored by Git) and
is rebuilt automatically when the documents change.
"""

import argparse
import contextlib
import csv
import hashlib
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "hartwell-hr"
DOCS_DIR = DATA_DIR / "hr-docs"
EMPLOYEES_CSV = DATA_DIR / "employees.csv"
DB_DIR = HERE / "chroma_db"  # ignored by Git

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


def doc_files():
    return sorted(p for p in DOCS_DIR.rglob("*")
                  if p.is_file() and p.suffix.lower() in (".html", ".htm"))


def make_chunks():
    chunks = []
    for path in doc_files():
        source = path.relative_to(DOCS_DIR).as_posix()
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


def fingerprint():
    """Changes whenever a document is added, removed, or edited."""
    h = hashlib.sha256()
    for p in doc_files():
        st = p.stat()
        h.update(f"{p.relative_to(DOCS_DIR)}|{st.st_size}|{int(st.st_mtime)}".encode())
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Chroma database
# ---------------------------------------------------------------------------

def chroma_client():
    # Chroma needs SQLite 3.35+; this codespace's built-in SQLite is older.
    try:
        import pysqlite3
        sys.modules["sqlite3"] = pysqlite3
    except ImportError:
        pass
    import chromadb
    from chromadb.config import Settings

    return chromadb.PersistentClient(path=str(DB_DIR), settings=Settings(anonymized_telemetry=False))


def embedder():
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    return DefaultEmbeddingFunction()  # Chroma's built-in model (all-MiniLM-L6-v2)


def build_index(client):
    log(f"Indexing {DOCS_DIR} ...")
    chunks = make_chunks()
    if not chunks:
        sys.exit(f"No HTML documents found in {DOCS_DIR}")
    if COLLECTION in [c.name if hasattr(c, "name") else c for c in client.list_collections()]:
        client.delete_collection(COLLECTION)
    col = client.create_collection(
        COLLECTION, embedding_function=embedder(),
        metadata={"hnsw:space": "cosine", "fingerprint": fingerprint()},
    )
    col.add(ids=[c["id"] for c in chunks], documents=[c["text"] for c in chunks],
            metadatas=[c["meta"] for c in chunks])
    log(f"Done: {len(chunks)} chunks stored in {DB_DIR}")
    return col


def open_index(rebuild=False):
    """Open the database, building it if it's missing, out of date, or --rebuild was given."""
    client = chroma_client()
    if rebuild:
        return build_index(client)
    try:
        col = client.get_collection(COLLECTION, embedding_function=embedder())
    except Exception:
        log("No database yet, building it now.")
        return build_index(client)
    if (col.metadata or {}).get("fingerprint") != fingerprint():
        log("The HR documents have changed since the database was built; rebuilding.")
        return build_index(client)
    return col


# ---------------------------------------------------------------------------
# Employee records
# ---------------------------------------------------------------------------

def normalize_email(email):
    return email.strip().strip("<>").strip().lower()


def find_employee(email):
    """Return the employee's row from employees.csv, or None. Re-read on every call."""
    wanted = normalize_email(email)
    with EMPLOYEES_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if normalize_email(row["email"]) == wanted:
                return row
    return None


def balance_or_error(email):
    from mcp.server.mcpserver.exceptions import ToolError

    row = find_employee(email) if email.strip() else None
    if row is None:
        raise ToolError(
            f"No employee record matches '{email}'. Do not state, estimate, or guess any "
            "leave balance. Say that HR will confirm the balance, and flag the reply "
            "for human review."
        )
    return {
        "email": row["email"],
        "name": row["name"],
        "department": row["department"],
        "manager": row["manager"] or None,
        "vacation_days_left": int(row["vacation_days_left"]),
        "sick_days_left": int(row["sick_days_left"]),
        "personal_days_left": int(row["personal_days_left"]),
    }


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

def create_server(col, as_employee=None):
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    mcp = MCPServer("hartwell-hr")
    read_only = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)

    @mcp.tool(annotations=read_only)
    def search_policy(query: str) -> dict:
        """Search Hartwell Foods' HR policy documents (vacation and time off, parental and
        family leave, benefits enrollment, remote work, reporting workplace concerns).

        Returns the 4 most relevant document sections. Each result has the source file to
        cite, the document title, section, version, a similarity score (higher is closer),
        and the section text. Use only what the returned text says; if no result answers
        the question, say the policies don't cover it.
        """
        res = col.query(query_texts=[query], n_results=TOP_K,
                        include=["documents", "metadatas", "distances"])
        results = [
            {"source": meta["source"], "title": meta["title"], "section": meta["section"],
             "version": meta["version"], "score": round(1 - dist, 3), "text": text}
            for text, meta, dist in zip(res["documents"][0], res["metadatas"][0],
                                        res["distances"][0])
        ]
        return {"query": query, "results": results}

    if as_employee:
        def get_leave_balance() -> dict:
            return balance_or_error(as_employee)

        mcp.add_tool(
            get_leave_balance, annotations=read_only,
            description=(
                "Get the leave balance (vacation, sick, and personal days left) of the "
                "employee whose email is being answered. Takes no inputs: it can return only "
                "that one employee's balance, never anyone else's."
            ),
        )
    else:
        @mcp.tool(annotations=read_only)
        def get_leave_balance(email: str) -> dict:
            """Look up one employee's leave balance by their work email address
            (for HR staff). Returns name, department, manager, and vacation, sick, and
            personal days left. If no record matches, returns an error: then do not state
            any balance.
            """
            return balance_or_error(email)

    return mcp


def main():
    parser = argparse.ArgumentParser(description="Hartwell Foods HR MCP server (stdio).")
    parser.add_argument("--as-employee", metavar="EMAIL",
                        help="limit get_leave_balance to this one employee; it then takes no inputs")
    parser.add_argument("--rebuild", action="store_true",
                        help="rebuild the policy database and exit")
    args = parser.parse_args()

    # Keep anything Chroma or the embedding model prints off stdout (the MCP channel).
    with contextlib.redirect_stdout(sys.stderr):
        col = open_index(rebuild=args.rebuild)
        if args.rebuild:
            return
        col.query(query_texts=["warm up"], n_results=1)  # load the embedding model now
        if args.as_employee and not find_employee(args.as_employee):
            log(f"Warning: no employee record matches {args.as_employee}; "
                "get_leave_balance will return an error.")

    create_server(col, as_employee=args.as_employee).run("stdio")


if __name__ == "__main__":
    main()
