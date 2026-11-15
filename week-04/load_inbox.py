"""Load the sample HR emails into the Gmail sandbox inbox as unread messages.

Usage (from the top of the repository):
    .venv/bin/python week-04/load_inbox.py

What it does:
  - Signs in to Gmail over IMAP (imap.gmail.com) with the login in the
    AGENT_EMAIL environment variable (a GitHub Codespaces secret).
  - For each .eml file in week-04/hartwell-hr/sample-emails/, sets the Date
    header to now, adds a Message-ID, and APPENDs it to INBOX without the
    \\Seen flag, so it shows up as unread with today's date.
  - Nothing is sent: IMAP APPEND places the message directly in the mailbox.
    Running it again adds another copy of each message.

AGENT_EMAIL holds the address and the Gmail app password, in any of these forms:
    you@gmail.com your-app-password        (one line: space, comma, semicolon, or colon between)
    you@gmail.com                          (two lines)
    your-app-password
    email: you@gmail.com                   (labeled lines)
    password: your-app-password
Gmail requires an app password (2-Step Verification on, IMAP enabled).

The login is read only at run time; the password and address are never
printed, logged, or saved anywhere.
"""

import imaplib
import os
import re
import sys
import time
from email import policy
from email.parser import BytesParser
from email.utils import formatdate, make_msgid
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGIN_VAR = "AGENT_EMAIL"  # Codespaces secret
EMAILS_DIR = HERE / "hartwell-hr" / "sample-emails"
IMAP_HOST = "imap.gmail.com"
MAILBOX = "INBOX"

ADDRESS_KEYS = {"email", "address", "user", "username", "login"}
PASSWORD_KEYS = {"password", "app_password", "app password", "apppassword", "pass"}


def read_login():
    """Return (address, password) from AGENT_EMAIL. Never prints either value."""
    raw = os.environ.get(LOGIN_VAR, "")
    if not raw.strip():
        sys.exit(f"The {LOGIN_VAR} environment variable is not set in this codespace. "
                 "Add it as a Codespaces secret for this repository, then restart the codespace.")
    lines = [ln.strip() for ln in raw.replace("\\n", "\n").splitlines() if ln.strip()]

    address = password = None
    for ln in lines:  # "key: value" or "key=value" form
        for sep in (":", "="):
            key, found, value = ln.partition(sep)
            key = key.strip().lower()
            if found and key in ADDRESS_KEYS:
                address = value.strip()
            elif found and key in PASSWORD_KEYS:
                password = value.strip()
            if found and (key in ADDRESS_KEYS or key in PASSWORD_KEYS):
                break
    if address is None and password is None:
        if len(lines) >= 2:  # address on one line, password on the next
            address, password = lines[0], lines[1]
        elif len(lines) == 1:  # "address<separator>password" on one line
            m = re.match(r"([^\s,;:]+@[^\s,;:]+)[\s,;:]+(.+)$", lines[0])
            if m:
                address, password = m.group(1), m.group(2)

    if not address or "@" not in address or not password:
        sys.exit(f"Could not read an address and app password from {LOGIN_VAR}. "
                 "Expected the address and the password, e.g. on two lines.")
    return address.strip(), password.strip().replace(" ", "")  # Gmail shows app passwords with spaces


def prepare(path):
    """Read one .eml file and give it today's date and a Message-ID."""
    msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    del msg["Date"]
    msg["Date"] = formatdate(localtime=True)
    if not msg["Message-ID"]:
        msg["Message-ID"] = make_msgid(idstring=path.stem, domain="hartwellfoods.example")
    return msg.as_bytes(policy=policy.SMTP)  # CRLF line endings, as IMAP expects


def main():
    files = sorted(EMAILS_DIR.glob("*.eml"))
    if not files:
        sys.exit(f"No .eml files found in {EMAILS_DIR}")
    address, password = read_login()

    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
    except OSError as e:
        sys.exit(f"Could not connect to {IMAP_HOST}: {e}")
    try:
        try:
            imap.login(address, password)
        except imaplib.IMAP4.error:
            sys.exit("Gmail rejected the login. Check the address and app password in "
                     f"{LOGIN_VAR}, and that IMAP is enabled for the account.")
        finally:
            del password
        print(f"Signed in to {IMAP_HOST}. Adding {len(files)} messages to {MAILBOX} as unread:")

        added = 0
        for path in files:
            status, _ = imap.append(MAILBOX, "", imaplib.Time2Internaldate(time.time()),
                                    prepare(path))
            print(f"  {'added ' if status == 'OK' else 'FAILED'}  {path.name}")
            added += status == "OK"
        print(f"Done: {added} of {len(files)} messages added. Nothing was sent.")
    finally:
        try:
            imap.logout()
        except Exception:
            pass


if __name__ == "__main__":
    main()
