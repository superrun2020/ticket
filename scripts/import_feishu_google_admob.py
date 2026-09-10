#!/usr/bin/env python3
"""Import Google/AdMob-related messages from a Feishu Mail IMAP account into tickets.

Password is read only from an environment variable (default: FEISHU_MAIL_PASSWORD).
No mailbox password is written to disk.
"""
from __future__ import annotations

import argparse
import email
import imaplib
import os
import ssl
import sys
from datetime import timezone
from email.message import Message
from email.utils import parsedate_to_datetime, parseaddr
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

IncomingAttachment: Any = None
IncomingMail: Any = None
db: Any = None
init_db: Any = None
now: Any = None
receive_mail: Any = None
_attachments: Any = None
_body: Any = None
_decode: Any = None

MAILBOX_ID = "google-admob"
MAILBOX_NAME = "谷歌 AdMob"
MAILBOX_EMAIL = "android@geekforest.ai"
MAILBOX_TAG = "谷歌AdMob专区"
GOOGLE_TERMS = (
    "google", "admob", "adsense", "ad manager", "google ads", "payments-noreply",
    "play console", "firebase", "google payments", "google account",
)
GOOGLE_DOMAINS = (
    "google.com", "googlemail.com", "withgoogle.com", "googlepayments.com",
    "admob.com", "adsense.com", "googleads.com",
)


def ensure_mailbox(email_address: str) -> None:
    ts = now()
    with db() as conn:
        conn.execute(
            """INSERT INTO mailboxes(id,name,email,color,created_at,enabled,workspace_id,mailbox_tag)
               VALUES(?,?,?,'#4285f4',?,1,'geekforest',?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name,email=excluded.email,enabled=1,
               workspace_id='geekforest',mailbox_tag=excluded.mailbox_tag""",
            (MAILBOX_ID, MAILBOX_NAME, email_address.lower(), ts, MAILBOX_TAG),
        )
        conn.execute("INSERT OR IGNORE INTO mailbox_sync(mailbox_id,last_uid,updated_at) VALUES(?,0,?)", (MAILBOX_ID, ts))


def message_date(msg: Message) -> str | None:
    raw = msg.get("Date")
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def is_google_message(msg: Message) -> bool:
    sender_name, sender_email = parseaddr(_decode(msg.get("From")))
    sender_email = sender_email.lower()
    headers = " ".join(
        _decode(msg.get(name, "")) for name in ("From", "Sender", "Reply-To", "Return-Path", "Subject")
    ).lower()
    if any(sender_email.endswith("@" + domain) or sender_email == domain for domain in GOOGLE_DOMAINS):
        return True
    return any(term in headers for term in GOOGLE_TERMS)


def import_uid(client: imaplib.IMAP4_SSL, uid: bytes, *, dry_run: bool) -> dict:
    uid_text = uid.decode()
    typ, fetched = client.uid("fetch", uid_text, "(RFC822)")
    if typ != "OK" or not fetched or not isinstance(fetched[0], tuple):
        return {"uid": uid_text, "status": "fetch_failed"}
    msg = email.message_from_bytes(fetched[0][1])
    if not is_google_message(msg):
        return {"uid": uid_text, "status": "skipped_non_google"}
    sender_name, sender_email = parseaddr(_decode(msg.get("From")))
    if not sender_email or "@" not in sender_email:
        return {"uid": uid_text, "status": "skipped_no_sender"}
    subject = _decode(msg.get("Subject")) or "（无主题）"
    if dry_run:
        return {"uid": uid_text, "status": "matched", "from": sender_email, "subject": subject[:160]}
    result = receive_mail(IncomingMail(
        mailbox_id=MAILBOX_ID,
        sender_name=(sender_name or sender_email)[:120],
        sender_email=sender_email,
        subject=subject[:300],
        body=(_body(msg) or "（无正文）")[:20_000],
        provider_message_id=f"feishu-google-admob:{uid_text}",
        internet_message_id=(msg.get("Message-ID") or "")[:1000] or None,
        references_header=((msg.get("References") or msg.get("In-Reply-To") or "")[:4000] or None),
        historical=True,
        received_at=message_date(msg),
        attachments=[IncomingAttachment(**item) for item in _attachments(msg)],
    ))
    return {"uid": uid_text, "status": "imported", "ticket_id": result.get("ticket_id"), "duplicate": result.get("duplicate", False)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Google/AdMob Feishu Mail messages into ticket-system")
    parser.add_argument("--email", default=MAILBOX_EMAIL, help="Feishu mailbox email/login")
    parser.add_argument("--host", default=os.getenv("FEISHU_IMAP_HOST", "imap.feishu.cn"), help="IMAP host")
    parser.add_argument("--port", type=int, default=int(os.getenv("FEISHU_IMAP_PORT", "993")), help="IMAP SSL port")
    parser.add_argument("--folder", default=os.getenv("FEISHU_IMAP_FOLDER", "INBOX"), help="IMAP folder")
    parser.add_argument("--password-env", default="FEISHU_MAIL_PASSWORD", help="environment variable holding the password")
    parser.add_argument("--limit", type=int, default=0, help="max messages to scan from newest to oldest; 0 means all")
    parser.add_argument("--dry-run", action="store_true", help="scan and report matches without creating tickets")
    return parser.parse_args()


def main() -> int:
    global IncomingAttachment, IncomingMail, db, init_db, now, receive_mail, _attachments, _body, _decode
    args = parse_args()
    password = os.environ.get(args.password_env, "")
    if not password:
        print(f"missing password environment variable: {args.password_env}", file=sys.stderr)
        return 2
    from app import IncomingAttachment as AppIncomingAttachment, IncomingMail as AppIncomingMail
    from app import db as app_db, init_db as app_init_db, now as app_now, receive_mail as app_receive_mail
    from mail_worker import _attachments as worker_attachments, _body as worker_body, _decode as worker_decode

    IncomingAttachment = AppIncomingAttachment
    IncomingMail = AppIncomingMail
    db = app_db
    init_db = app_init_db
    now = app_now
    receive_mail = app_receive_mail
    _attachments = worker_attachments
    _body = worker_body
    _decode = worker_decode
    init_db()
    ensure_mailbox(args.email)
    client = imaplib.IMAP4_SSL(args.host, args.port, ssl_context=ssl.create_default_context())
    counts: dict[str, int] = {}
    try:
        client.login(args.email, password)
        typ, _ = client.select(args.folder)
        if typ != "OK":
            raise RuntimeError(f"cannot select folder {args.folder}")
        typ, data = client.uid("search", None, "ALL")
        if typ != "OK":
            raise RuntimeError("imap search failed")
        uids = data[0].split() if data and data[0] else []
        if args.limit > 0:
            uids = uids[-args.limit:]
        for index, uid in enumerate(reversed(uids), start=1):
            item = import_uid(client, uid, dry_run=args.dry_run)
            counts[item["status"]] = counts.get(item["status"], 0) + 1
            if item["status"] in {"matched", "imported"}:
                print(item)
            if index % 100 == 0:
                print({"scanned": index, "counts": counts})
        print({"ok": True, "scanned": len(uids), "counts": counts, "dry_run": args.dry_run})
        return 0
    finally:
        try:
            client.logout()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
