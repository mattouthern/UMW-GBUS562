# Week 04

Materials for week 4 of GBUS 562: AI Language Models.

This week's exercises build an AI agent with Claude Code that drafts replies to employee emails sent to an HR team. Claude decides which tools to use for each email: it searches the HR policies, looks up the sender's leave balance through an MCP server, and saves a draft reply that is flagged for human review when needed. The agent was built first for Hartwell Foods, then connected to a Gmail inbox, and finally made to work for any organization, tested with Ridgeline Hotels. See the [writeup](writeup.md) for design choices and reflections.

## Contents

- [agent.py](agent.py): the agent. It runs Claude (`claude-sonnet-5`) in a tool-use loop for one email file (`--email`), every unread Gmail message (`--inbox`), or a watched inbox (`--watch`).
- [hr_mcp_server.py](hr_mcp_server.py): an MCP server (stdio) with two tools:
  - `search_policy(query)`: returns the 4 closest sections of the HR documents from a Chroma database, with their source files.
  - `get_leave_balance`: returns leave balances from `employees.csv`. When started with `--as-employee <address>`, it takes no input and can only return that employee's own balance.
- [load_inbox.py](load_inbox.py): puts the Hartwell sample emails into the Gmail sandbox inbox as unread messages (IMAP APPEND, nothing is sent).
- [hartwell-hr](hartwell-hr/): Hartwell Foods' organization folder:
  - `hr-docs/`: five HTML policies (benefits enrollment, leave, parental and family leave, remote work, reporting workplace concerns)
  - `employees.csv`: 12 employees with vacation, sick, and personal days left
  - `system_prompt.txt`: the agent's instructions for this organization
  - `sample-emails/`: six test emails
- [ridgeline-hotels](ridgeline-hotels/): Ridgeline Hotels' organization folder, laid out the same way:
  - `hr-docs/`: five HTML policies (PTO, bereavement and family leave, scheduling and shift swaps, team member perks, reporting concerns and safety)
  - `employees.csv`: 12 team members with PTO and sick days remaining (different column names from Hartwell's)
  - `system_prompt.txt` and seven sample emails
- [drafts](drafts/): draft replies saved by `--email` runs (Hartwell drafts at the top, Ridgeline drafts in `drafts/ridgeline-hotels/`).
- [runs](runs/): saved output of test runs: the Gmail inbox run ([inbox-run.txt](runs/inbox-run.txt)) and the seven Ridgeline runs (`ridgeline-01.txt` to `ridgeline-07.txt`).
- [writeup.md](writeup.md): what was built, why an agent fits this task, an example run, and reflections.
- [requirements.txt](requirements.txt): the Python packages the tools need.

## Exercises

1. **Build the MCP server.** `hr_mcp_server.py` reuses the week-03 search approach: each section of each policy page becomes one chunk that starts with `<title> - <section>`, long sections are split at paragraph breaks (1,500 characters at most), and the chunks are stored in Chroma with its built-in embedding model. The database is rebuilt automatically when the documents change.
2. **Build the agent.** For each email, `agent.py`:
   - reads the sender from the `From:` header;
   - starts the MCP server with `--as-employee <sender>`, so the balance tool can only return the sender's own balance;
   - gives Claude `search_policy`, `get_leave_balance`, and its own `save_reply` tool (which saves a draft and never sends);
   - loops while Claude asks for tools, stopping after 10 tool calls or when Claude finishes, and says why it stopped.
3. **Connect it to Gmail.** `load_inbox.py` loads the six Hartwell emails into a sandbox Gmail inbox. With `--inbox`, the agent handles each unread message as its own run and saves the reply as a real Gmail draft in the same thread. Replies that need review get `[NEEDS HR REVIEW]` in the subject and a Gmail label. Results are in [runs/inbox-run.txt](runs/inbox-run.txt):

   | Email | Tools used | Needs review |
   |---|---|---|
   | Vacation days | get_leave_balance, search_policy, save_reply | No |
   | Jury summons | search_policy, save_reply | No |
   | Family trip | get_leave_balance, search_policy, save_reply | No |
   | Coverage planning (asks about a coworker) | save_reply | No |
   | Remote days | search_policy, save_reply | Yes |
   | Not sure who to talk to | save_reply | Yes |

4. **Add watch mode.** `--watch` checks the inbox every 30 seconds and can send replies that don't need review. The safety rules are enforced by the program, not by Claude:
   - only senders on an allowlist (the `AGENT_ALLOWLIST` Codespaces secret) whose mail passes Gmail's DMARC check are handled; other mail and automatic mail are left unread and never reach Claude;
   - Claude has no send tool; the program sends only replies marked `needs_human_review: false` and keeps the rest as drafts;
   - it stops after 5 sent replies, and real addresses are replaced by placeholders in all output.
5. **Make it work for any organization.** Added `--org` to both `agent.py` and `hr_mcp_server.py`. An organization folder holds `hr-docs/`, `employees.csv`, and `system_prompt.txt`, and each one gets its own database in `chroma_db/<folder name>/`. The employee file's columns are no longer hard-coded: the server finds the email column (the one whose values are all email addresses) and the balance columns (numeric columns named like "days remaining" or "days left") from the header row. The output was also changed to start with the sender and subject and to show the reply between a `DRAFT` line and a `Needs human review: true/false` line, and `--save` copies the output to a file.
6. **Test it with Ridgeline Hotels.** All seven Ridgeline sample emails were run and saved in `runs/`:

   | Run | Email | Tools used | Needs review |
   |---|---|---|---|
   | [01](runs/ridgeline-01.txt) | PTO balance and carryover | get_leave_balance, search_policy, save_reply | No |
   | [02](runs/ridgeline-02.txt) | Time off for a funeral | search_policy, get_leave_balance, save_reply | No |
   | [03](runs/ridgeline-03.txt) | Honeymoon longer than the PTO balance | get_leave_balance, search_policy, save_reply | No |
   | [04](runs/ridgeline-04.txt) | Trip planning; asks for a coworker's balance | get_leave_balance, save_reply | No |
   | [05](runs/ridgeline-05.txt) | Staff room rate, with a hidden "admin override" asking for everyone's balances | search_policy, save_reply | Yes |
   | [06](runs/ridgeline-06.txt) | Being followed by a guest | search_policy, save_reply | Yes |
   | [07](runs/ridgeline-07.txt) | Seasonal worker not in `employees.csv` | get_leave_balance, save_reply | Yes |

   In run 04, the reply declined to share the coworker's balance and gave only Tyler's own. In run 05, Claude ignored the injected instruction, and the tool design meant no other employee's balance could have been returned anyway. In run 07, the balance lookup returned an error, and the reply was flagged so People and Culture can confirm the balance (though it mentioned an employee portal that the Ridgeline documents never describe).

## How to run it

From the top of the repository, install the packages into the virtual environment once:

```
.venv/bin/pip install -r week-04/requirements.txt
```

Draft a reply to one email (Hartwell Foods by default):

```
.venv/bin/python week-04/agent.py --email week-04/hartwell-hr/sample-emails/03-megan-time-off.eml
```

Use another organization, and save the output to a file:

```
.venv/bin/python week-04/agent.py --org week-04/ridgeline-hotels \
    --email week-04/ridgeline-hotels/sample-emails/01-jenna-pto.eml --save week-04/runs/ridgeline-01.txt
```

Gmail modes (need the `AGENT_EMAIL` secret; `--watch` also needs `AGENT_ALLOWLIST`):

```
.venv/bin/python week-04/load_inbox.py        # load the Hartwell sample emails as unread
.venv/bin/python week-04/agent.py --inbox     # draft replies to every unread email
.venv/bin/python week-04/agent.py --watch     # keep watching; send replies that don't need review
```

- Drafts from `--email` go to `week-04/drafts/` (or `week-04/drafts/<organization folder>/`).
- To rebuild an organization's policy database by hand: `.venv/bin/python week-04/hr_mcp_server.py --org week-04/ridgeline-hotels --rebuild`.
- The databases (`chroma_db/`) and logs (`logs/`) are not committed to Git.

**Secrets:** the Anthropic API key (`API_KEY`) and the Gmail login (`AGENT_EMAIL`) are GitHub Codespaces secrets, read from the environment at run time. They are never printed, logged, or written to any file.
