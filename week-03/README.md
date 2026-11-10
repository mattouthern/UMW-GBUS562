# Week 03

Materials for week 3 of GBUS 562: AI Language Models.

This week's exercises build a retrieval-augmented generation (RAG) tool with Claude Code. The tool answers questions about a folder of documents by finding the most relevant passages and having Claude answer only from them. It was built first for Copperline Manufacturing's finance documents, then reused for Larkfield's member-services documents.

## Contents

- [ask.py](ask.py): a Python command-line tool that answers questions from a folder of documents (Copperline by default), using Claude (`claude-sonnet-5`).
- [copperline-docs](copperline-docs/): the five finance documents the tool searches:
  - Corporate Card FAQ
  - Month-End Close Calendar
  - Purchasing Approval Matrix
  - Travel and Expense Policy (Version 4.0)
  - Vendor Payment Terms (PDF)
- [copperline-docs-archive](copperline-docs-archive/): the superseded Travel and Expense Policy (Version 3.2). It is kept separate so the tool never answers from an outdated policy.
- [larkfield-docs](larkfield-docs/): the five Larkfield member-services documents (all HTML):
  - Consumer Fee Schedule (Version 8)
  - Debit Card Dispute Procedure
  - Fee Refund Guide
  - Funds Availability Policy
  - Share Certificates
- [larkfield-docs-archive](larkfield-docs-archive/): the superseded Consumer Fee Schedule (Version 7), moved out of the search folder.
- [runs](runs/): saved output (retrieved chunks and Claude's answer) from the seven Larkfield test runs.
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

4. **Point the tool at the Larkfield documents.** Unzipped `larkfield-docs.zip` and added a `--docs` option so `ask.py` can search any folder. Each folder gets its own database under `chroma_db/`. The Larkfield pages use the same section-by-section chunking: each `<h2>`/`<h3>` section is one chunk, tables are turned into text rows, and any section over 1,500 characters is split at paragraph breaks.
5. **Find and fix an outdated document.** The zip held two versions of the Consumer Fee Schedule, and both were marked current. Version 7 lists a $35 overdraft fee; Version 8 lists $29.
   - **Before** ([q1-before.txt](runs/q1-before.txt)): asking about the overdraft fee pulled two chunks from each version. Claude gave the right answer ($29) but also cited the old $35 fee.
   - **Fix:** moved Version 7 into `larkfield-docs-archive/` and rebuilt the database.
   - **After** ([q1-after.txt](runs/q1-after.txt)): all four chunks came from Version 8, and the answer was $29 with no conflicting fee.
6. **Test the tool with more questions.** Each run is saved in `runs/`:

   | Run | Topic | Answer |
   |---|---|---|
   | [q2](runs/q2.txt) | Rush delivery of a replacement debit card | $30 |
   | [q3](runs/q3.txt) | Member's liability for a lost card reported the same day | At most $50 |
   | [q4](runs/q4.txt) | Can a representative refund a $45 international wire fee alone? | No; refunds over $30 need branch manager approval |
   | [q5](runs/q5.txt) | How much of a $2,000 check deposited at a branch is available next day | $275 next business day; $1,725 the day after |
   | [q6](runs/q6.txt) | Mortgage rates (not covered by the documents) | Declined: the documents don't cover mortgage rates |

   Questions q4 and q5 needed facts from two sections or a calculation, and Claude combined them correctly. In q6, the retrieved chunks had low similarity scores (0.18–0.25), and Claude said the answer wasn't in the documents rather than guessing.

## How to run it

From the top of the repository, install the packages into the virtual environment once:

```
.venv/bin/pip install -r week-03/requirements.txt
```

Then ask a question:

```
.venv/bin/python week-03/ask.py "Can I expense a $70 dinner in Chicago?"
```

- Add `--docs week-03/larkfield-docs` to search the Larkfield documents instead, and `--save week-03/runs/<name>.txt` to save the output to a file.
- Leave out the question to ask several in a row; press Enter on an empty line to quit.
- After adding or removing documents in a folder, add `--rebuild` to update that folder's database. The tool handles HTML, PDF, Markdown, and plain-text files.
- The database is built automatically on the first run and stored in `week-03/chroma_db/`, which is not committed to Git.

**API key:** the tool reads your Anthropic API key from `API-Key.txt` in the folder that contains the repository. If that file doesn't exist, it uses the `API_KEY` environment variable instead. The key is never written to any file in the repository.
