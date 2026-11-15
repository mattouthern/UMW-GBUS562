"""HR email agent: prepares draft replies to employee emails using Claude's tool use.

Usage (from the top of the repository):
    # One email from a file; the draft is written to week-04/drafts/
    .venv/bin/python week-04/agent.py --email week-04/hartwell-hr/sample-emails/03-megan-time-off.eml

    # Every unread email in the Gmail inbox; each draft becomes a real Gmail draft
    .venv/bin/python week-04/agent.py --inbox

    # Keep watching the inbox; send replies that don't need review (see --watch below)
    .venv/bin/python week-04/agent.py --watch

    # Any mode can also copy everything it prints to a UTF-8 text file
    .venv/bin/python week-04/agent.py --inbox --save week-04/logs/inbox-run.txt

How it works (for each email):
  1. Takes the sender's address from the email's From: header.
  2. Starts week-04/hr_mcp_server.py with --as-employee <sender>, so the
     get_leave_balance tool can only ever return the sender's own balance.
  3. Gives Claude the server's tools (search_policy, get_leave_balance) plus
     save_reply, which is defined here.
  4. Loops: Claude asks for a tool, the agent runs it and returns the result.
     It stops after 10 tool calls, or as soon as Claude replies without asking
     for a tool, and says why.

--inbox mode:
  - Signs in to Gmail over IMAP with the same AGENT_EMAIL login as load_inbox.py.
  - Handles each unread INBOX message one at a time, as its own separate agent run.
    Each message is marked read BEFORE the agent works on it, so no email is
    handled twice (to retry one, mark it unread in Gmail and run again).
  - save_reply creates a Gmail draft (IMAP APPEND to the Drafts folder) addressed
    to the sender, with In-Reply-To/References set so it joins the same thread.
    If needs_human_review is true, the subject starts with [NEEDS HR REVIEW] and the
    thread also gets the Gmail label NEEDS HR REVIEW (Gmail's conversation view shows
    the original subject, which hides the tag on the draft's subject).
  - This program only reads mail and creates drafts. It has no way to send email.

--watch mode (rules enforced by this program, not by Claude):
  - Every 30 seconds, looks for unread INBOX mail and handles it as in --inbox.
  - Reads an allowlist from the AGENT_ALLOWLIST environment variable (a Codespaces
    secret, beside AGENT_EMAIL). One entry per line (or separated by , or ;):
        your.address@gmail.com = dana.whitfield@hartwellfoods.example
    The employee address must be in hartwell-hr/employees.csv.
  - Before anything else, only the headers are read (without marking the message read):
      * automatic mail (any Auto-Submitted header, or Precedence: bulk / auto_reply)
        is ignored and left unread;
      * mail from an address not on the allowlist is left unread and never sent to Claude;
      * mail from an allowed address is used only if Gmail's own Authentication-Results
        header shows dmarc=pass for the sender's domain, so a forged From: is left unread.
  - An allowed sender is treated as the employee on their line: Claude, the MCP server
    (--as-employee) and all output see only the employee address.
  - After save_reply: if needs_human_review is false, the reply is sent with Gmail SMTP
    (same AGENT_EMAIL login), marked Auto-Submitted: auto-replied. Otherwise it is kept as a
    Gmail draft, as in --inbox. Claude has no send tool; sending happens in this program.
  - Stops after 5 sent replies, or on Ctrl+C.
  - The real sender addresses, the agent's address and the password are replaced by
    placeholders in everything printed (and in --save files), and are removed from the
    email text before it goes to Claude.

--email mode keeps the original behavior: save_reply writes a draft file to week-04/drafts/.

The system prompt is read from week-04/hartwell-hr/system_prompt.txt on every run.
The API key (API_KEY) and the Gmail login (AGENT_EMAIL) are read from the environment
at run time and are never printed, logged, or saved.
"""

import argparse
import asyncio
import imaplib
import json
import os
import re
import smtplib
import sys
import time
import traceback
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, getaddresses, make_msgid, parseaddr
from pathlib import Path

import anthropic
from bs4 import BeautifulSoup
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from hr_mcp_server import find_employee
from load_inbox import IMAP_HOST, LOGIN_VAR, read_login

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SERVER = HERE / "hr_mcp_server.py"
SYSTEM_PROMPT_FILE = HERE / "hartwell-hr" / "system_prompt.txt"
DRAFTS_DIR = HERE / "drafts"
SERVER_LOG = HERE / "logs" / "mcp-server.log"  # the server's stderr; ignored by Git

MODEL = "claude-sonnet-5"
MAX_TOOL_CALLS = 10
PREVIEW_CHARS = 300
INBOX = "INBOX"
REVIEW_TAG = "[NEEDS HR REVIEW]"
REVIEW_LABEL = "NEEDS HR REVIEW"  # Gmail label put on threads whose draft needs review

ALLOWLIST_VAR = "AGENT_ALLOWLIST"  # Codespaces secret (GitHub stores secret names in capitals)
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
WATCH_SECONDS = 30
MAX_SENT = 5
AUTO_PRECEDENCE = {"bulk", "auto_reply"}

SAVE_REPLY_TOOL = {
    "name": "save_reply",
    "description": (
        "Save the finished reply to the employee as a draft for the HR team to review. "
        "This only saves a draft; it never sends anything. Call it exactly once, "
        "when the reply is complete."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reply": {
                "type": "string",
                "description": "The full text of the reply to the employee.",
            },
            "needs_human_review": {
                "type": "boolean",
                "description": "true if a person from HR must review or follow up before "
                               "anything is sent, otherwise false.",
            },
        },
        "required": ["reply", "needs_human_review"],
        "additionalProperties": False,
    },
    "strict": True,
}

# --watch mode: same tool, but the description tells Claude truthfully what happens next.
SAVE_REPLY_WATCH_TOOL = {
    **SAVE_REPLY_TOOL,
    "description": (
        "Save the finished reply to the employee. If needs_human_review is false, this "
        "program emails the reply to the employee right away. If it is true, the reply is "
        "kept as a draft for the HR team to review and nothing is sent. Call it exactly "
        "once, when the reply is complete."
    ),
}


# ---------------------------------------------------------------------------
# Reading an email
# ---------------------------------------------------------------------------

def parse_email(raw):
    """Return a dict with the sender, subject, body and threading headers of raw email bytes.

    Returns None for the sender if the From: header has no usable address.
    """
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    _, sender = parseaddr(str(msg.get("From", "")))
    sender = sender.strip().lower()
    part = msg.get_body(preferencelist=("plain", "html"))
    body = part.get_content() if part else ""
    if part is not None and part.get_content_type() == "text/html":
        body = BeautifulSoup(body, "html.parser").get_text("\n")
    return {
        "sender": sender if "@" in sender else None,
        "subject": " ".join(str(msg.get("Subject", "")).split()),
        "body": body.strip(),
        "message_id": str(msg.get("Message-ID", "")).strip(),
        "references": " ".join(str(msg.get("References", "")).split()),
    }


def reply_subject(subject, needs_review):
    subject = subject or "(no subject)"
    subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    return f"{REVIEW_TAG} {subject}" if needs_review else subject


def check_reply_args(args, state):
    """Return an error message for bad save_reply input, or None if it is fine."""
    if state["draft"]:
        return "A reply was already saved for this email. Do not call save_reply again."
    reply, review = args.get("reply", ""), args.get("needs_human_review")
    if not isinstance(reply, str) or not reply.strip() or not isinstance(review, bool):
        return "save_reply needs a non-empty reply and needs_human_review true or false."
    return None


# ---------------------------------------------------------------------------
# Draft savers. Each takes (reply, needs_review) and returns (result for Claude,
# printable copy of the draft). Neither one can send email.
# ---------------------------------------------------------------------------

def file_saver(email_path, email):
    """--email mode: write the draft to week-04/drafts/<email file name>.txt."""
    def save(reply, review):
        DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        path = DRAFTS_DIR / f"{email_path.stem}.txt"
        text = (
            "DRAFT - NOT SENT\n"
            f"To: {email['sender']}\n"
            f"Subject: {reply_subject(email['subject'], review)}\n"
            f"Needs human review: {'YES' if review else 'no'}\n"
            f"In reply to: {email_path.name}\n"
            f"Drafted: {datetime.now().isoformat(timespec='seconds')} by {MODEL}\n"
            "\n" + reply.strip() + "\n"
        )
        path.write_text(text, encoding="utf-8")
        shown = path.relative_to(REPO) if path.is_relative_to(REPO) else path
        return f"Draft saved to {shown} (not sent).", f"Saved to {shown}\n\n{text}"
    return save


class GmailInbox:
    """IMAP connection to the Gmail account in AGENT_EMAIL. Reads mail and creates drafts only."""

    def __init__(self):
        self.imap = None
        self.address = None   # used only as the draft's From:, never printed
        self.drafts = None

    def connect(self):
        address, password = read_login()
        try:
            imap = imaplib.IMAP4_SSL(IMAP_HOST)
        except OSError as e:
            sys.exit(f"Could not connect to {IMAP_HOST}: {e}")
        try:
            imap.login(address, password)
        except imaplib.IMAP4.error:
            sys.exit("Gmail rejected the login. Check the address and app password in "
                     f"{LOGIN_VAR}, and that IMAP is enabled for the account.")
        finally:
            del password
        self.imap, self.address = imap, address
        self.drafts = self.drafts or self._find_drafts_folder()
        self._check(self.imap.select(INBOX), f"open {INBOX}")

    def ensure_connected(self):
        """Reconnect if Gmail dropped the connection while the agent was working."""
        try:
            self.imap.noop()
        except (imaplib.IMAP4.abort, OSError):
            print("(IMAP connection was closed; signing in again.)")
            self.connect()

    def logout(self):
        if self.imap is not None:
            try:
                self.imap.logout()
            except Exception:
                pass

    @staticmethod
    def _check(result, what):
        status, data = result
        if status != "OK":
            detail = data[0].decode(errors="replace") if data and isinstance(data[0], bytes) else ""
            sys.exit(f"IMAP could not {what}. {detail}".strip())
        return data

    def _find_drafts_folder(self):
        """Return the mailbox Gmail marks as \\Drafts (its name depends on the account language)."""
        for line in self._check(self.imap.list(), "list mailboxes"):
            if not isinstance(line, bytes):
                continue
            m = re.match(rb'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$', line)
            if m and b"\\drafts" in m.group("flags").lower().split():
                return m.group("name").decode().strip('"')
        return "[Gmail]/Drafts"

    def peek_headers(self, uid):
        """Return one message's headers without marking it read, or None if it is gone."""
        data = self._check(self.imap.uid("FETCH", uid, "(BODY.PEEK[HEADER])"),
                           "read message headers")
        for item in data:
            if isinstance(item, tuple):
                return BytesParser(policy=policy.default).parsebytes(item[1], headersonly=True)
        return None

    def unread_uids(self):
        data = self._check(self.imap.uid("SEARCH", None, "UNSEEN"), "search for unread mail")
        return data[0].split() if data and data[0] else []

    def claim(self, uid):
        """Mark one message read and return its raw bytes, or None if it was already read.

        The message is marked read before anything else happens, so it is never
        handled twice.
        """
        data = self._check(self.imap.uid("FETCH", uid, "(FLAGS)"), "read message flags")
        if not data or data[0] is None or b"\\Seen" in data[0]:
            return None
        self._check(self.imap.uid("STORE", uid, "+FLAGS.SILENT", "(\\Seen)"), "mark message read")
        data = self._check(self.imap.uid("FETCH", uid, "(BODY.PEEK[])"), "download message")
        for item in data:
            if isinstance(item, tuple):
                return item[1]
        return None

    def label_for_review(self, uid):
        """Put the REVIEW_LABEL Gmail label on the original message, so the whole thread
        shows it in Gmail. (Gmail shows a thread's first subject, which hides the tag on
        the draft's subject.) Returns True if Gmail accepted it."""
        try:
            self.ensure_connected()
            status, _ = self.imap.uid("STORE", uid, "+X-GM-LABELS", f'("{REVIEW_LABEL}")')
            return status == "OK"
        except (imaplib.IMAP4.error, OSError):
            return False

    def send(self, msg):
        """Send one message with Gmail SMTP, using the AGENT_EMAIL login.

        Error messages never include addresses or the password.
        """
        address, password = read_login()
        try:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=60) as smtp:
                smtp.login(address, password)
                smtp.send_message(msg)
        except smtplib.SMTPAuthenticationError:
            raise RuntimeError(f"{SMTP_HOST} rejected the login") from None
        except smtplib.SMTPResponseException as e:
            raise RuntimeError(f"{SMTP_HOST} refused the message (code {e.smtp_code})") from None
        except (smtplib.SMTPException, OSError) as e:
            raise RuntimeError(f"could not send through {SMTP_HOST} ({type(e).__name__})") from None
        finally:
            del password

    def _reply_message(self, email, review):
        msg = EmailMessage()
        msg["From"] = self.address
        msg["To"] = email.get("reply_to") or email["sender"]
        msg["Subject"] = reply_subject(email["subject"], review)
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=self.address.split("@", 1)[1])
        if email["message_id"]:
            msg["In-Reply-To"] = email["message_id"]
            msg["References"] = " ".join(
                x for x in (email["references"], email["message_id"]) if x)
        return msg

    def saver(self, email, uid, budget=None):
        """Create a Gmail draft replying to the sender in the same thread.

        --watch passes budget (a dict with "sent"): then a reply with needs_human_review
        false is sent with SMTP instead, as long as fewer than MAX_SENT have been sent.
        """
        def save(reply, review):
            msg = self._reply_message(email, review)
            msg.set_content(reply.strip() + "\n")
            to_shown = email["sender"] + (" (via their allowlist address)"
                                          if email.get("reply_to") else "")

            note = ""
            if budget is not None and not review:
                if budget["sent"] >= MAX_SENT:
                    note = f"Not sent: the limit of {MAX_SENT} sent replies was reached.\n"
                else:
                    msg["Auto-Submitted"] = "auto-replied"  # RFC 3834: stops reply loops
                    try:
                        self.send(msg)
                    except RuntimeError as e:
                        del msg["Auto-Submitted"]
                        note = f"Sending failed ({e}); kept as a draft instead.\n"
                    else:
                        budget["sent"] += 1
                        shown = (f"SENT with Gmail SMTP (reply {budget['sent']} of {MAX_SENT})\n"
                                 f"To: {to_shown}\nSubject: {msg['Subject']}\n"
                                 f"In-Reply-To: {msg['In-Reply-To'] or '(original had no Message-ID)'}\n"
                                 "Needs human review: no\n"
                                 "\n" + reply.strip() + "\n")
                        return "The reply was emailed to the employee.", shown

            self.ensure_connected()
            status, data = self.imap.append(
                f'"{self.drafts}"', "(\\Draft \\Seen)",
                imaplib.Time2Internaldate(time.time()), msg.as_bytes(policy=policy.SMTP))
            if status != "OK":
                detail = data[0].decode(errors="replace") if data and data[0] else ""
                raise RuntimeError(f"Gmail did not accept the draft. {detail}".strip())

            label_note = ""
            if review:
                label_note = (f"Gmail label: {REVIEW_LABEL}\n" if self.label_for_review(uid)
                              else f"Gmail label: could not add {REVIEW_LABEL}\n")
            shown = (f"Gmail draft in {self.drafts} (not sent)\n"
                     f"{note}{label_note}"
                     f"To: {to_shown}\nSubject: {msg['Subject']}\n"
                     f"In-Reply-To: {msg['In-Reply-To'] or '(original had no Message-ID)'}\n"
                     f"Needs human review: {'YES' if review else 'no'}\n"
                     "\n" + reply.strip() + "\n")
            return f"Draft saved in Gmail's {self.drafts} folder (not sent).", shown
        return save


# ---------------------------------------------------------------------------
# Tools and the agent loop
# ---------------------------------------------------------------------------

def mcp_to_claude_tool(tool):
    schema = dict(tool.input_schema or {})
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return {"name": tool.name, "description": tool.description or "", "input_schema": schema}


async def call_mcp_tool(session, name, args):
    """Run a tool on the MCP server. Returns (result text, is_error)."""
    result = await session.call_tool(name, args)
    text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
    if not text and result.structured_content is not None:
        text = json.dumps(result.structured_content)
    return text, bool(result.is_error)


def save_reply(args, saver, state):
    """Handle a save_reply tool call. Returns (result text, is_error)."""
    error = check_reply_args(args, state)
    if error:
        return error, True
    try:
        result, shown = saver(args["reply"], args["needs_human_review"])
    except (OSError, RuntimeError, imaplib.IMAP4.error) as e:
        return f"The draft could not be saved: {e}", True
    state["draft"] = shown
    return result, False


def preview(text):
    text = " ".join(text.split())
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "..."


def stop_reason_message(response):
    reason = response.stop_reason
    if reason == "end_turn":
        return "Claude finished its turn without asking for another tool."
    if reason == "max_tokens":
        return "Claude's reply hit the max_tokens limit before finishing."
    if reason == "refusal":
        return "Claude declined to continue (refusal)."
    return f"Claude stopped without a tool request (stop_reason: {reason})."


async def run_agent(email, saver, client, save_tool=SAVE_REPLY_TOOL):
    """One agent run for one email. Returns True if a draft was saved."""
    sender, subject, body = email["sender"], email["subject"], email["body"]
    system_prompt = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()
    print(f"Sender:  {sender}")
    print(f"Subject: {subject}\n")

    user_msg = (
        "Prepare a reply to this employee email. The email is between the <email> tags.\n\n"
        f"<email>\nFrom: {sender}\nSubject: {subject}\n\n{body}\n</email>"
    )
    messages = [{"role": "user", "content": user_msg}]
    state = {"draft": None}
    calls = 0
    tokens_in = tokens_out = 0
    stop_why = None

    server = StdioServerParameters(
        command=sys.executable, args=[str(SERVER), "--as-employee", sender], cwd=str(REPO),
    )
    SERVER_LOG.parent.mkdir(parents=True, exist_ok=True)
    print("Starting hr_mcp_server.py ...")
    with SERVER_LOG.open("a", encoding="utf-8") as errlog:
        async with stdio_client(server, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                mcp_tools = (await session.list_tools()).tools
                mcp_names = {t.name for t in mcp_tools}
                tools = [mcp_to_claude_tool(t) for t in mcp_tools] + [save_tool]
                print(f"Tools: {', '.join(t['name'] for t in tools)}\n")

                while True:
                    try:
                        response = await client.messages.create(
                            model=MODEL, max_tokens=16000, system=system_prompt,
                            tools=tools, messages=messages,
                        )
                    except anthropic.AuthenticationError:
                        sys.exit("Claude rejected the API key in API_KEY.")
                    except anthropic.NotFoundError:
                        sys.exit(f"Model '{MODEL}' was not found.")
                    except anthropic.RateLimitError:
                        sys.exit("Rate limit reached. Wait a moment and try again.")
                    except anthropic.APIStatusError as e:
                        sys.exit(f"Claude API error ({e.status_code}): {e.message}")
                    except anthropic.APIConnectionError:
                        sys.exit("Could not connect to the Claude API.")

                    tokens_in += response.usage.input_tokens
                    tokens_out += response.usage.output_tokens
                    for block in response.content:
                        if block.type == "text" and block.text.strip():
                            print(f"Claude: {preview(block.text)}\n")

                    if response.stop_reason != "tool_use":
                        stop_why = stop_reason_message(response)
                        break

                    messages.append({"role": "assistant", "content": response.content})
                    results = []
                    for block in (b for b in response.content if b.type == "tool_use"):
                        if calls >= MAX_TOOL_CALLS:
                            stop_why = (f"Reached the limit of {MAX_TOOL_CALLS} tool calls; "
                                        f"Claude asked for another ({block.name}).")
                            break
                        calls += 1
                        print(f"Step {calls}: {block.name}")
                        print(f"  input:  {json.dumps(block.input)}")
                        if block.name == "save_reply":
                            text, is_error = save_reply(block.input, saver, state)
                        elif block.name in mcp_names:
                            text, is_error = await call_mcp_tool(session, block.name, block.input)
                        else:
                            text, is_error = f"Unknown tool: {block.name}", True
                        print(f"  result{' (error)' if is_error else ''}: {preview(text)}\n")
                        results.append({"type": "tool_result", "tool_use_id": block.id,
                                        "content": text, "is_error": is_error})
                    if stop_why:
                        break
                    messages.append({"role": "user", "content": results})

    print(f"Stopped: {stop_why}")
    print(f"Tool calls: {calls}\n")
    if state["draft"]:
        print("===== Draft =====")
        print(state["draft"])
    else:
        print("No draft was saved (save_reply was not called or failed).\n")
    print(f"Tokens: {tokens_in} input, {tokens_out} output")
    return state["draft"] is not None


async def run_email_file(email_path, client):
    email = parse_email(email_path.read_bytes())
    if not email["sender"]:
        sys.exit(f"Could not find a sender address in the From: header of {email_path}")
    print(f"Email:   {email_path.relative_to(REPO) if email_path.is_relative_to(REPO) else email_path}")
    await run_agent(email, file_saver(email_path, email), client)


async def run_inbox(client):
    inbox = GmailInbox()
    try:
        inbox.connect()
        uids = inbox.unread_uids()
        print(f"Signed in to {IMAP_HOST}. Unread emails in {INBOX}: {len(uids)}. "
              f"Drafts go to: {inbox.drafts}\n")
        drafted = skipped = 0
        for n, uid in enumerate(uids, 1):
            print(f"========== Email {n} of {len(uids)} (IMAP UID {uid.decode()}) ==========")
            inbox.ensure_connected()
            raw = inbox.claim(uid)
            if raw is None:
                print("Already read (handled elsewhere); skipping.\n")
                skipped += 1
                continue
            print("Marked as read.")
            email = parse_email(raw)
            if not email["sender"]:
                print("No sender address in the From: header; skipping.\n")
                skipped += 1
                continue
            try:
                drafted += await run_agent(email, inbox.saver(email, uid), client)
            except SystemExit:
                print("This email was marked read but has no draft. "
                      "Mark it unread in Gmail to retry it.")
                raise
            print()
        print(f"Done: {len(uids)} unread emails, {drafted} drafts created, {skipped} skipped. "
              "Nothing was sent.")
    finally:
        inbox.logout()


# ---------------------------------------------------------------------------
# --watch: allowlist, sender checks, and the polling loop
# ---------------------------------------------------------------------------

def read_allowlist():
    """Return {allowed sender address: employee address} from AGENT_ALLOWLIST.

    Error messages give line numbers only, never the addresses on the line.
    """
    raw = os.environ.get(ALLOWLIST_VAR) or os.environ.get("Agent_Allowlist") or ""
    if not raw.strip():
        sys.exit(f"The {ALLOWLIST_VAR} environment variable is not set in this codespace. "
                 "Add it as a Codespaces secret for this repository, then restart the codespace.")
    entries = [e.strip() for e in re.split(r"[\n,;]", raw.replace("\\n", "\n"))]
    allow = {}
    for n, entry in enumerate((e for e in entries if e and not e.startswith("#")), 1):
        real, sep, employee = entry.partition("=")
        real = real.strip().strip("<>").strip().lower()
        employee = employee.strip().strip("<>").strip().lower()
        if not sep or "@" not in real or "@" not in employee:
            sys.exit(f"Entry {n} of {ALLOWLIST_VAR} is not in the form "
                     "'your.address@gmail.com = employee@hartwellfoods.example'.")
        if not find_employee(employee):
            sys.exit(f"Entry {n} of {ALLOWLIST_VAR}: {employee} is not in employees.csv.")
        if allow.get(real, employee) != employee:
            sys.exit(f"Entry {n} of {ALLOWLIST_VAR} lists a sender that is already "
                     "listed for a different employee.")
        allow[real] = employee
    if not allow:
        sys.exit(f"{ALLOWLIST_VAR} has no entries.")
    return allow


def gmail_verified(headers, address):
    """True if Gmail's own (topmost) Authentication-Results header shows DMARC passed
    for the From: domain. Headers lower down could have been written by the sender."""
    results = headers.get_all("Authentication-Results") or []
    if not results:
        return False
    top = " ".join(str(results[0]).split()).lower()
    domain = re.escape(address.rsplit("@", 1)[1])
    return (top.startswith("mx.google.com;")
            and re.search(r"(^|[\s;])dmarc=pass\b", top) is not None
            and re.search(rf"\bheader\.from={domain}(?=$|[\s;)])", top) is not None)


def check_sender(headers, allowlist, own_address):
    """Decide from the headers alone whether an email may go to Claude.

    Returns (sender address, employee address, None) if it may, otherwise
    (None, None, reason). Reasons never contain addresses.
    """
    if headers.get("Auto-Submitted") is not None:
        return None, None, "automatic mail (Auto-Submitted header); ignored, left unread"
    precedence = str(headers.get("Precedence", "")).strip().lower()
    if precedence in AUTO_PRECEDENCE:
        return None, None, f"automatic mail (Precedence: {precedence}); ignored, left unread"
    froms = getaddresses([str(h) for h in headers.get_all("From") or []])
    if len(froms) != 1:
        return None, None, "not exactly one From: address; left unread"
    address = froms[0][1].strip().lower()
    if address == own_address.lower():
        return None, None, "sent from the agent's own address; left unread"
    employee = allowlist.get(address)
    if employee is None:
        return None, None, "sender is not on the allowlist; left unread, not sent to Claude"
    if not gmail_verified(headers, address):
        return None, None, ("Gmail did not confirm the sender (no dmarc=pass), so the From: "
                            "may be forged; left unread, not sent to Claude")
    return address, employee, None


class Redact:
    """Output stream that replaces private strings (real addresses, password) with
    placeholders before anything reaches the terminal or a --save file."""

    def __init__(self, stream, replacements):
        self.stream = stream
        self.map = {k.lower(): v for k, v in replacements.items() if k}
        keys = sorted(self.map, key=len, reverse=True)
        self.pattern = re.compile("|".join(re.escape(k) for k in keys), re.IGNORECASE)

    def scrub(self, text):
        return self.pattern.sub(lambda m: self.map[m.group(0).lower()], text)

    def write(self, text):
        return self.stream.write(self.scrub(text))

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def install_redaction(allowlist):
    """Wrap stdout and stderr so private values can never be printed. Returns the scrubber."""
    address, password = read_login()
    replacements = {real: f"<allowed sender for {emp}>" for real, emp in allowlist.items()}
    replacements[address] = "<agent address>"
    replacements[password] = "<password>"
    out = Redact(sys.stdout, replacements)
    sys.stdout, sys.stderr = out, Redact(sys.stderr, replacements)
    del password
    return out.scrub


def now():
    return datetime.now().strftime("%H:%M:%S")


async def run_watch(client, allowlist, scrub):
    inbox = GmailInbox()
    budget = {"sent": 0}
    passed_over = set()   # UIDs left unread on purpose; not reported again
    handled = 0
    current = None        # UID being worked on, for the Ctrl+C message
    try:
        inbox.connect()
        employees = ", ".join(sorted(set(allowlist.values())))
        print(f"Signed in to {IMAP_HOST}. Watching {INBOX} every {WATCH_SECONDS} seconds.")
        print(f"Allowed senders: {len(allowlist)} (as {employees}).")
        print(f"Replies not needing review are sent; others go to {inbox.drafts}. "
              f"Stops after {MAX_SENT} sent replies, or press Ctrl+C.\n")
        waiting_shown = False
        while budget["sent"] < MAX_SENT:
            inbox.ensure_connected()  # its NOOP also lets new mail show up
            for uid in inbox.unread_uids():
                if uid in passed_over:
                    continue
                waiting_shown = False
                headers = inbox.peek_headers(uid)
                if headers is None:
                    continue
                address, employee, why = check_sender(headers, allowlist, inbox.address)
                if why:
                    print(f"[{now()}] Email UID {uid.decode()}: {why}.")
                    passed_over.add(uid)
                    continue

                print(f"========== [{now()}] Email UID {uid.decode()} "
                      f"from the allowed sender for {employee} ==========")
                current = uid
                raw = inbox.claim(uid)
                if raw is None:
                    print("Already read (handled elsewhere); skipping.\n")
                    current = None
                    continue
                print("Marked as read.")
                email = parse_email(raw)
                email.update(sender=employee, reply_to=address,
                             subject=scrub(email["subject"]), body=scrub(email["body"]))
                await run_agent(email, inbox.saver(email, uid, budget), client,
                                SAVE_REPLY_WATCH_TOOL)
                current = None
                handled += 1
                print(f"Sent so far: {budget['sent']} of {MAX_SENT}.\n")
                if budget["sent"] >= MAX_SENT:
                    break
            if budget["sent"] >= MAX_SENT:
                print(f"Reached the limit of {MAX_SENT} sent replies; stopping.")
                break
            if not waiting_shown:
                print(f"[{now()}] Waiting for new mail (checking every {WATCH_SECONDS} s; "
                      "Ctrl+C to stop) ...")
                waiting_shown = True
            await asyncio.sleep(WATCH_SECONDS)
    finally:
        if current is not None:
            print(f"\nEmail UID {current.decode()} was marked read but may have no reply. "
                  "Mark it unread in Gmail to retry it.")
        print(f"Watch ended: {handled} emails handled, {budget['sent']} replies sent, "
              f"{len(passed_over)} emails left unread.")
        inbox.logout()


# ---------------------------------------------------------------------------
# --save: copy everything printed to a text file
# ---------------------------------------------------------------------------

class Tee:
    """Write to the terminal and to a log file at the same time."""

    def __init__(self, stream, log):
        self.stream, self.log = stream, log

    def write(self, text):
        self.stream.write(text)
        self.log.write(text)
        return len(text)

    def flush(self):
        self.stream.flush()
        self.log.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def main():
    parser = argparse.ArgumentParser(description="Draft replies to HR emails with Claude.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--email", metavar="FILE", help="path of one email file (.eml)")
    source.add_argument("--inbox", action="store_true",
                        help="handle every unread email in the Gmail inbox (AGENT_EMAIL login)")
    source.add_argument("--watch", action="store_true",
                        help=f"check the inbox every {WATCH_SECONDS} s, only for senders in "
                             f"{ALLOWLIST_VAR}; send replies that need no review "
                             f"(at most {MAX_SENT}); Ctrl+C stops")
    parser.add_argument("--save", metavar="FILE",
                        help="also write everything printed to this text file (UTF-8)")
    args = parser.parse_args()

    log = None
    if args.save:
        save_path = Path(args.save).expanduser()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        log = save_path.open("w", encoding="utf-8")
        sys.stdout = Tee(sys.__stdout__, log)
        sys.stderr = Tee(sys.__stderr__, log)
    try:
        api_key = os.environ.get("API_KEY", "").strip()
        if not api_key:
            sys.exit("The API_KEY environment variable is not set.")
        client = anthropic.AsyncAnthropic(api_key=api_key)

        if args.watch:
            allowlist = read_allowlist()
            scrub = install_redaction(allowlist)
            try:
                asyncio.run(run_watch(client, allowlist, scrub))
            except KeyboardInterrupt:
                print("Stopped by Ctrl+C.")
        elif args.inbox:
            asyncio.run(run_inbox(client))
        else:
            email_path = Path(args.email).expanduser().resolve()
            if not email_path.is_file():
                sys.exit(f"Email file not found: {args.email}")
            asyncio.run(run_email_file(email_path, client))
    except SystemExit as e:
        if isinstance(e.code, str):  # print the message now, so --save captures it too
            print(e.code, file=sys.stderr)
            raise SystemExit(1) from None
        raise
    except Exception:
        traceback.print_exc()  # printed here, while --watch redaction is still in place
        raise SystemExit(1) from None
    finally:
        if log is not None:
            sys.stdout.flush()
            sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
            log.close()
            print(f"Output saved to {args.save}")


if __name__ == "__main__":
    main()
