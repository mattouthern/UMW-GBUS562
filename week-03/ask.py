"""Ask questions about a folder of documents (RAG). Defaults to Copperline's finance documents.

Usage (from the top of the repository):
    .venv/bin/python week-03/ask.py "How much can I spend on dinner in Boston?"
    .venv/bin/python week-03/ask.py              # interactive: ask several questions
    .venv/bin/python week-03/ask.py --rebuild    # re-index after adding/removing documents
    .venv/bin/python week-03/ask.py --docs week-03/larkfield-docs "What is the overdraft fee?"
    .venv/bin/python week-03/ask.py "..." --save week-03/runs/q1.txt   # also save the output

How it works:
  1. Every document in the folder (copperline-docs/ unless --docs names another)
     is split into one chunk per section. Each chunk starts with
     "<document title> - <section heading>" so it makes sense on its own, and
     records the file it came from.
  2. Chunks are stored in a Chroma database using Chroma's built-in embedding
     model. Each document folder gets its own database under week-03/chroma_db/
     (which Git ignores).
  3. For a question, the 4 most similar chunks are retrieved and shown, then
     sent to Claude with instructions to answer only from them and cite files.
"""

import argparse
import hashlib
import os
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DOCS_DIR = HERE / "copperline-docs"
DB_ROOT = HERE / "chroma_db"  # one sub-folder per document folder; ignored by Git
# Set by use_docs_folder() from the --docs option.
DOCS_DIR = DEFAULT_DOCS_DIR
DB_DIR = None
# API-Key.txt lives in the folder that contains the repository (outside Git).
# If it's missing, the API_KEY environment variable is used instead.
KEY_FILE = HERE.parent.parent / "API-Key.txt"

COLLECTION = "docs"
MODEL = "claude-sonnet-5"
TOP_K = 4
MAX_CHUNK_CHARS = 1500  # longer sections are split at paragraph boundaries
SUPPORTED = {".html", ".htm", ".pdf", ".md", ".txt"}

# Extra context added to every chunk of a document, for terms that a document
# defines once but uses in every section. Keyed by document folder name, then file.
CONTEXT_NOTES = {
    "copperline-docs": {
        "month-end-close-calendar.html": (
            "Deadlines are counted in business days: business day 1 is the first "
            "business day of the new month, and business day minus 1 is the last "
            "business day of the month being closed."
        ),
    },
}


def use_docs_folder(folder):
    """Point the tool at a document folder and its own Chroma database."""
    global DOCS_DIR, DB_DIR
    DOCS_DIR = Path(folder).expanduser().resolve()
    if not DOCS_DIR.is_dir():
        sys.exit(f"Document folder not found: {folder}")
    # Name + short hash of the full path, so two folders with the same name never share a database.
    tag = hashlib.sha256(str(DOCS_DIR).encode()).hexdigest()[:8]
    DB_DIR = DB_ROOT / f"{DOCS_DIR.name}-{tag}"


def show_path(path):
    """Path relative to the repository when possible, for friendlier messages."""
    try:
        return path.relative_to(HERE.parent).as_posix()
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Loading documents: each loader returns (title, meta, [(heading, body), ...])
# ---------------------------------------------------------------------------

def clean(text):
    return re.sub(r"\s+", " ", text).strip()


def load_html(path):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    root = soup.find("main") or soup.body or soup
    for tag in root.find_all(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    h1 = root.find("h1")
    title = clean(h1.get_text()) if h1 else (clean(soup.title.get_text()) if soup.title else path.stem)
    title = title.split("|")[0].strip()  # "Corporate Card FAQ | Copperline" -> "Corporate Card FAQ"
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


def load_pdf(path):
    """Find headings by font: bold lines larger than the body text."""
    import pdfplumber

    lines = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for ln in page.extract_text_lines(return_chars=True):
                c = ln["chars"][0]
                lines.append({"text": ln["text"].strip(), "size": round(c["size"], 1),
                              "bold": "bold" in c["fontname"].lower(), "top": ln["top"],
                              "page": page.page_number})
    if not lines:
        return path.stem, "", []

    body = Counter(l["size"] for l in lines).most_common(1)[0][0]
    # Drop footers/page numbers (smaller than body text or "Page N of M").
    lines = [l for l in lines if l["text"] and l["size"] >= body
             and not re.search(r"Page \d+ of \d+", l["text"])]
    big = sorted({l["size"] for l in lines if l["bold"] and l["size"] > body})
    section_size = big[0] if big else None

    first_sec = next((i for i, l in enumerate(lines) if l["size"] == section_size), len(lines))
    larger = [i for i in range(first_sec) if lines[i]["size"] > (section_size or body)]
    if larger:  # title = last big line before the first section (skips the letterhead)
        title_i = larger[-1]
        title = lines[title_i]["text"]
        meta = clean(" ".join(l["text"] for l in lines[title_i + 1:first_sec]))
    else:
        title, meta = path.stem.replace("-", " ").title(), ""

    sections, heading, paras, prev = [], "Introduction", [], None
    for l in lines[first_sec:]:
        if l["size"] == section_size:
            if paras:
                sections.append((heading, "\n".join(paras)))
            heading, paras, prev = l["text"], [], None
            continue
        # New paragraph on a bullet or a big vertical gap; otherwise rejoin the wrapped line.
        new_para = (not paras or prev is None or l["page"] != prev["page"]
                    or l["top"] - prev["top"] > 1.8 * body or l["text"][:1] in "•-–")
        if new_para:
            paras.append(l["text"])
        else:
            paras[-1] += " " + l["text"]
        prev = l
    if paras:
        sections.append((heading, "\n".join(paras)))
    return title, meta, sections


def load_markdown(path):
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^#\s+(.+)$", text, re.M)
    title = m.group(1).strip() if m else path.stem.replace("-", " ").title()
    sections, heading, parts = [], "Introduction", []
    for line in text.splitlines():
        if re.match(r"^#{2,6}\s+", line):
            if clean(" ".join(parts)):
                sections.append((heading, "\n".join(parts).strip()))
            heading, parts = line.lstrip("#").strip(), []
        elif not re.match(r"^#\s+", line):
            parts.append(line)
    if clean(" ".join(parts)):
        sections.append((heading, "\n".join(parts).strip()))
    return title, "", sections


def load_text(path):
    paras = [clean(p) for p in re.split(r"\n\s*\n", path.read_text(encoding="utf-8")) if clean(p)]
    title = path.stem.replace("-", " ").replace("_", " ").title()
    return title, "", [("Full text", "\n".join(paras))] if paras else []


LOADERS = {".html": load_html, ".htm": load_html, ".pdf": load_pdf,
           ".md": load_markdown, ".txt": load_text}


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

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
    files = sorted(p for p in DOCS_DIR.rglob("*") if p.is_file() and not p.name.startswith("."))
    for p in files:
        if p.suffix.lower() not in SUPPORTED:
            print(f"  Skipping {p.name}: unsupported file type ({p.suffix or 'none'})")
    return [p for p in files if p.suffix.lower() in SUPPORTED]


def make_chunks():
    chunks = []
    for path in doc_files():
        source = path.relative_to(DOCS_DIR).as_posix()
        try:
            title, meta, sections = LOADERS[path.suffix.lower()](path)
        except Exception as e:
            print(f"  Skipping {source}: could not read it ({e})")
            continue
        note = CONTEXT_NOTES.get(DOCS_DIR.name, {}).get(source, "")
        count = 0
        for heading, body in sections:
            pieces = split_long(body)
            for n, piece in enumerate(pieces, 1):
                label = heading if len(pieces) == 1 else f"{heading} (part {n} of {len(pieces)})"
                header = f"{title} - {label}"
                extra = "\n".join(x for x in (meta, note) if x)
                text = f"{header}\n{extra}\n\n{piece}" if extra else f"{header}\n\n{piece}"
                chunks.append({
                    "id": f"{source}::{count}",
                    "text": text,
                    "meta": {"source": source, "title": title, "section": label,
                             "version": meta, "chunk": count},
                })
                count += 1
        print(f"  {source}: {count} chunks")
    return chunks


def fingerprint():
    """Changes whenever a document is added, removed, or edited."""
    h = hashlib.sha256()
    for p in doc_files() if DOCS_DIR.exists() else []:
        st = p.stat()
        h.update(f"{p.relative_to(DOCS_DIR)}|{st.st_size}|{int(st.st_mtime)}".encode())
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Chroma database
# ---------------------------------------------------------------------------

def chroma_client():
    # Chroma needs SQLite 3.35+; this codespace's built-in SQLite is older,
    # so use the newer copy from the pysqlite3-binary package when available.
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


def build_index():
    print(f"Indexing documents in {show_path(DOCS_DIR)}/ ...")
    chunks = make_chunks()
    if not chunks:
        sys.exit("No documents found to index.")
    client = chroma_client()
    if COLLECTION in [c.name if hasattr(c, "name") else c for c in client.list_collections()]:
        client.delete_collection(COLLECTION)
    col = client.create_collection(
        COLLECTION, embedding_function=embedder(),
        metadata={"hnsw:space": "cosine", "fingerprint": fingerprint()},
    )
    col.add(ids=[c["id"] for c in chunks], documents=[c["text"] for c in chunks],
            metadatas=[c["meta"] for c in chunks])
    print(f"Done: {len(chunks)} chunks stored in {show_path(DB_DIR)}/\n")
    return col


def open_index():
    client = chroma_client()
    try:
        col = client.get_collection(COLLECTION, embedding_function=embedder())
    except Exception:
        print("No database yet, building it now.")
        return build_index()
    if (col.metadata or {}).get("fingerprint") != fingerprint():
        print(f"Note: the documents in {show_path(DOCS_DIR)}/ have changed since the "
              "database was built. Run with --rebuild to update it.\n")
    return col


# ---------------------------------------------------------------------------
# Retrieval + Claude
# ---------------------------------------------------------------------------

def retrieve(col, question):
    res = col.query(query_texts=[question], n_results=TOP_K,
                    include=["documents", "metadatas", "distances"])
    hits = []
    for text, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        hits.append({"text": text, "meta": meta, "score": 1 - dist})  # cosine similarity
    return hits


def show_hits(hits):
    print(f"Top {len(hits)} sources:")
    for rank, h in enumerate(hits, 1):
        preview = clean(h["text"])[:150]
        print(f"  {rank}. score {h['score']:.3f} | {h['meta']['source']}")
        print(f"     {preview}...")
    print()


SYSTEM_PROMPT = """You answer questions about an organization's documents.

Use ONLY the sources provided in the user's message. Do not use outside knowledge or assumptions.
- After each fact you state, cite the source file it came from in square brackets, e.g. [example-policy.html].
- If the sources do not contain the answer, say clearly that the provided documents don't answer the question. Do not guess.
- If the sources answer only part of the question, answer that part and say what is missing.
- Keep the answer concise and in plain language."""


def read_api_key():
    """Use API-Key.txt if it exists; otherwise fall back to the API_KEY environment variable."""
    if KEY_FILE.is_file():
        key = KEY_FILE.read_text(encoding="utf-8").strip()
        if not key:
            sys.exit(f"API key file is empty: {KEY_FILE}")
        return key
    key = os.environ.get("API_KEY", "").strip()
    if not key:
        sys.exit(f"No API key found. Create {KEY_FILE} with your Anthropic API key "
                 "as its only content, or set the API_KEY environment variable.")
    return key


def ask_claude(client, question, hits):
    import anthropic

    sources = "\n\n".join(
        f'<source rank="{i}" file="{h["meta"]["source"]}">\n{h["text"]}\n</source>'
        for i, h in enumerate(hits, 1)
    )
    user_msg = f"<sources>\n{sources}\n</sources>\n\nQuestion: {question}"
    try:
        response = client.messages.create(
            model=MODEL, max_tokens=4096, system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
    except anthropic.AuthenticationError:
        sys.exit("Claude rejected the API key. Check API-Key.txt or the API_KEY variable.")
    except anthropic.NotFoundError:
        sys.exit(f"Model '{MODEL}' was not found.")
    except anthropic.RateLimitError:
        sys.exit("Rate limit reached. Wait a moment and try again.")
    except anthropic.APIStatusError as e:
        sys.exit(f"Claude API error ({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        sys.exit("Could not connect to the Claude API. Check your internet connection.")

    if response.stop_reason == "refusal":
        return "Claude declined to answer this question."
    return "\n".join(b.text for b in response.content if b.type == "text").strip()


def save_run(path, hits, reply):
    """Write the full retrieved chunks, then a line with only ANSWER, then Claude's answer."""
    parts = []
    for rank, h in enumerate(hits, 1):
        parts.append(f"Rank {rank} | score {h['score']:.3f} | source {h['meta']['source']}\n"
                     f"{h['text']}\n")
    parts.append(f"ANSWER\n{reply}\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")
    print(f"Saved this run to {show_path(path)}")


def answer(col, client, question, save_to=None):
    hits = retrieve(col, question)
    show_hits(hits)
    reply = ask_claude(client, question, hits)
    print("Answer:")
    print(reply)
    print()
    if save_to:
        save_run(save_to, hits, reply)


def main():
    parser = argparse.ArgumentParser(description="Ask questions about a folder of documents.")
    parser.add_argument("question", nargs="*", help="your question (leave out for interactive mode)")
    parser.add_argument("--docs", metavar="FOLDER", default=str(DEFAULT_DOCS_DIR),
                        help="folder of documents to answer from (default: week-03/copperline-docs)")
    parser.add_argument("--rebuild", action="store_true",
                        help="rebuild the database after adding or removing documents")
    parser.add_argument("--save", metavar="FILE",
                        help="also write the retrieved chunks and the answer to this text file")
    args = parser.parse_args()

    question = " ".join(args.question).strip()
    if args.save and not question:
        parser.error("--save needs a question on the command line")
    save_to = Path(args.save).expanduser().resolve() if args.save else None
    use_docs_folder(args.docs)

    col = build_index() if args.rebuild else None
    if args.rebuild and not question:
        return

    import anthropic

    claude = anthropic.Anthropic(api_key=read_api_key())
    col = col or open_index()

    if question:
        answer(col, claude, question, save_to)
        return
    print(f"Ask a question about the documents in {show_path(DOCS_DIR)}/ "
          "(press Enter on an empty line to quit).")
    while (q := input("\nQuestion: ").strip()):
        print()
        answer(col, claude, q)


if __name__ == "__main__":
    main()
