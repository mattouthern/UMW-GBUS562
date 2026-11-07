# Week 03

Materials for week 3 of GBUS 562: AI Language Models.

This week's exercises build a retrieval-augmented generation (RAG) tool with Claude Code. The tool answers questions about Copperline Manufacturing's finance documents by finding the most relevant passages and having Claude answer only from them.

## Contents

- [ask.py](ask.py): a Python command-line tool that answers questions from the Copperline documents, using Claude (`claude-sonnet-5`).
- [copperline-docs](copperline-docs/): the five finance documents the tool searches:
  - Corporate Card FAQ
  - Month-End Close Calendar
  - Purchasing Approval Matrix
  - Travel and Expense Policy (Version 4.0)
  - Vendor Payment Terms (PDF)
- [copperline-docs-archive](copperline-docs-archive/): the superseded Travel and Expense Policy (Version 3.2). It is kept separate so the tool never answers from an outdated policy.
- [requirements.txt](requirements.txt): the Python packages the tool needs.

## Exercises

1. **Set up the documents.** Unzipped `copperline-docs.zip` from Canvas into this folder.
2. **Plan the chunking.** Looked at each file type and how it is structured, then chose how to split the documents so every chunk makes sense on its own:
   - The documents are four HTML pages and one PDF. All five have a title, a version/owner line, and short sections under headings.
   - Each section becomes one chunk. Fixed-size chunks would cut lists away from the sentences that introduce them.
   - Every chunk starts with its document's title and section heading (for example, *Travel and Expense Policy - Meal limits by city tier*) and records the file it came from.
   - PDF headings are found by their bold, larger font, and lines broken by the page layout are joined back together.
3. **Build the tool.** `ask.py` stores the 35 chunks in a Chroma database using Chroma's built-in embedding model. For each question it:
   - retrieves the 4 most relevant chunks;
   - prints each one's rank, similarity score, source file, and first 150 characters;
   - sends the question and those chunks to Claude, which answers only from them, cites the source file for each fact, and says so when the documents don't contain the answer.

## How to run it

From the top of the repository, install the packages into the virtual environment once:

```
.venv/bin/pip install -r week-03/requirements.txt
```

Then ask a question:

```
.venv/bin/python week-03/ask.py "Can I expense a $70 dinner in Chicago?"
```

- Leave out the question to ask several in a row; press Enter on an empty line to quit.
- After adding or removing documents in `copperline-docs`, run `.venv/bin/python week-03/ask.py --rebuild` to update the database. The tool handles HTML, PDF, Markdown, and plain-text files.
- The database is built automatically on the first run and stored in `week-03/chroma_db/`, which is not committed to Git.

**API key:** the tool reads your Anthropic API key from `API-Key.txt` in the folder that contains the repository. If that file doesn't exist, it uses the `API_KEY` environment variable instead. The key is never written to any file in the repository.
