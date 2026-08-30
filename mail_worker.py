from __future__ import annotations

import email
import base64
import hashlib
import html
import imaplib
import json
import logging
import os
import re
import smtplib
import sqlite3
import ssl
import threading
import time
from datetime import datetime, timezone
from email.header import decode_header
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path
from cryptography.fernet import Fernet

log = logging.getLogger("ticket-mail")
TICKET_RE = re.compile(r"\[(TKT-\d+)\]", re.I)


def _delivery_report(msg: email.message.Message) -> tuple[str, str, str] | None:
    """Return (internet_message_id, status, detail) for a standards-based DSN."""
    if msg.get_content_type() != "multipart/report" and "delivery status" not in (_decode(msg.get("Subject"))).lower():
        return None
    report = "\n".join(str(part) for part in msg.walk() if part.get_content_type() == "message/delivery-status")
    if not report:
        report = _body(msg)
    original = re.search(r"(?:Original-Message-ID|X-Original-Message-ID):\s*(<[^>]+>)", report, re.I)
    action = re.search(r"Action:\s*(delivered|relayed|expanded|failed|delayed)", report, re.I)
    diagnostic = re.search(r"Diagnostic-Code:\s*([^\r\n]+)", report, re.I)
    if not original or not action:
        return None
    value = action.group(1).lower()
    status = "delivered" if value in {"delivered", "relayed", "expanded"} else "failed" if value == "failed" else "sent"
    return original.group(1), status, (diagnostic.group(1).strip() if diagnostic else value)[:500]


def _read_receipt(msg: email.message.Message) -> tuple[str, str] | None:
    """Return (internet_message_id, detail) for a standards-based MDN read receipt."""
    content_type = msg.get_content_type().lower()
    report_type = (msg.get_param("report-type") or "").lower()
    if content_type != "multipart/report" or report_type != "disposition-notification":
        return None
    report_parts = [str(part) for part in msg.walk() if part.get_content_type() == "message/disposition-notification"]
    report = "\n".join(report_parts) or _body(msg)
    original = re.search(r"Original-Message-ID:\s*(<[^>]+>)", report, re.I)
    disposition = re.search(r"Disposition:\s*([^\r\n]+)", report, re.I)
    if not original or not disposition or not re.search(r"\bdisplayed\b", disposition.group(1), re.I):
        return None
    return original.group(1), disposition.group(1).strip()[:500]


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    return "".join((x.decode(enc or "utf-8", "replace") if isinstance(x, bytes) else x) for x, enc in parts)


def _body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in (part.get("Content-Disposition") or ""):
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace").strip()
        return ""
    return msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", "replace").strip()


def _eddy_configs() -> list[dict]:
    if os.getenv("EDDY_SOURCE_ENABLED", "0") != "1" or not os.getenv("EDDY_SOURCE_DB_HOST"):
        return []
    import pymysql
    conn = pymysql.connect(host=os.environ["EDDY_SOURCE_DB_HOST"], port=int(os.getenv("EDDY_SOURCE_DB_PORT", "3306")),
        user=os.environ["EDDY_SOURCE_DB_USER"], password=os.environ["EDDY_SOURCE_DB_PASSWORD"],
        database=os.getenv("EDDY_SOURCE_DB_NAME", "dev_perform"), connect_timeout=8, read_timeout=15,
        cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as cursor:
            cursor.execute("""SELECT id,project_code,display_name,mailbox_email,imap_host,imap_port,imap_username,
                smtp_host,smtp_port,smtp_encryption,smtp_username,password_ciphertext,mail_folder,mailbox_tag
                FROM eddy_ticket_mailboxes WHERE enabled=1""")
            rows = cursor.fetchall()
    finally:
        conn.close()
    key = base64.urlsafe_b64encode(hashlib.sha256(os.environ["TICKET_SESSION_SECRET"].encode()).digest())
    box = Fernet(key)
    return [{"id": f"eddy-{r['id']}", "name": r["display_name"] or r["project_code"], "email": r["mailbox_email"],
        "color": "#1b9aaa", "imap_host": r["imap_host"], "imap_port": r["imap_port"], "imap_folder": r["mail_folder"],
        "username": r["imap_username"], "smtp_username": r["smtp_username"], "password": box.decrypt(r["password_ciphertext"].encode()).decode(),
        "smtp_host": r["smtp_host"], "smtp_port": r["smtp_port"], "smtp_ssl": r["smtp_encryption"] == "ssl",
        "workspace_id": "eddy-personal", "mailbox_tag": r["mailbox_tag"] or "未分类"} for r in rows]


def _project_config(row: dict) -> dict:
    """Normalize DB mailbox rows, including Gmail/Google Workspace host split."""
    imap_host = (row["mail_host"] or "").strip()
    email_address = (row["mailbox_email"] or "").strip()
    is_google = "gmail" in imap_host.lower() or "googlemail" in imap_host.lower()
    password = row["password"]
    if is_google and password:
        # Google displays app passwords grouped with spaces; IMAP/SMTP expects the token.
        password = str(password).replace(" ", "")
    return {
        "id": f"project-{row['id']}", "name": row["project_code"], "email": email_address,
        "color": "#6558d3", "imap_host": "imap.gmail.com" if is_google else imap_host,
        "imap_port": 993 if is_google else row["mail_port"], "imap_folder": row["mail_folder"],
        "username": row["mail_username"] or email_address, "password": password,
        "smtp_host": "smtp.gmail.com" if is_google else imap_host,
        "smtp_port": 465 if is_google else int(os.getenv("TICKET_SMTP_PORT", "465")),
        "smtp_ssl": True if is_google else os.getenv("TICKET_SMTP_SSL", "1") == "1",
        "workspace_id": os.getenv("TICKET_SOURCE_WORKSPACE_ID", "geekforest"),
        "mailbox_tag": "未分类", "provider": "google" if is_google else "standard",
    }


def _managed_google_configs(root: Path) -> list[dict]:
    path = root / "data" / "tickets.db"
    if not path.exists():
        return []
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = list(conn.execute("""SELECT id,project_code,mailbox_email,password_ciphertext,mailbox_tag,workspace_id
            FROM managed_google_mailboxes WHERE enabled=1"""))
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    key = base64.urlsafe_b64encode(hashlib.sha256(os.environ["TICKET_SESSION_SECRET"].encode()).digest())
    box = Fernet(key)
    configs = []
    for row in rows:
        configs.append({
            "id": row["id"], "name": row["project_code"], "email": row["mailbox_email"], "color": "#4285f4",
            "imap_host": "imap.gmail.com", "imap_port": 993, "imap_folder": "INBOX",
            "username": row["mailbox_email"], "smtp_username": row["mailbox_email"],
            "password": box.decrypt(row["password_ciphertext"].encode()).decode(),
            "smtp_host": "smtp.gmail.com", "smtp_port": 465, "smtp_ssl": True,
            "workspace_id": row["workspace_id"], "mailbox_tag": row["mailbox_tag"], "provider": "google",
        })
    return configs


def load_configs(root: Path) -> list[dict]:
    if os.getenv("TICKET_MAILBOX_SOURCE") == "mysql":
        import pymysql
        conn = pymysql.connect(
            host=os.environ["TICKET_SOURCE_DB_HOST"],
            port=int(os.getenv("TICKET_SOURCE_DB_PORT", "3306")),
            user=os.environ["TICKET_SOURCE_DB_USER"],
            password=os.environ["TICKET_SOURCE_DB_PASSWORD"],
            database=os.getenv("TICKET_SOURCE_DB_NAME", "nxpanel"),
            connect_timeout=8,
            read_timeout=15,
            cursorclass=pymysql.cursors.DictCursor,
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute("""SELECT id,project_code,mailbox_email,mail_host,mail_port,
                    mail_protocol,mail_encryption,mail_username,
                    COALESCE(NULLIF(mail_password_plain,''),mail_password) AS password,
                    mail_folder,enabled FROM project_mailboxes WHERE enabled=1""")
                rows = cursor.fetchall()
        finally:
            conn.close()
        configs = [_project_config(r) for r in rows]
        return configs + _managed_google_configs(root) + _eddy_configs()
    path = Path(os.getenv("TICKET_MAILBOXES_FILE", root / "mailboxes.json"))
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [x for x in data if x.get("enabled", True)] + _managed_google_configs(root) + _eddy_configs()


class MailWorker:
    def __init__(self, root: Path, db_factory, receive):
        self.root, self.db, self.receive = root, db_factory, receive
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.config_cursor = 0

    def start(self):
        if os.getenv("TICKET_MAIL_WORKER", "1") == "0" or self.thread:
            return
        self.thread = threading.Thread(target=self._loop, daemon=True, name="ticket-mail-worker")
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                configs = load_configs(self.root)
                self._sync_config(configs)
                per_cycle = max(1, int(os.getenv("TICKET_MAILBOXES_PER_CYCLE", "5")))
                if len(configs) > per_cycle:
                    start = self.config_cursor % len(configs)
                    selected = (configs + configs)[start:start + per_cycle]
                    self.config_cursor = (start + per_cycle) % len(configs)
                else:
                    selected = configs
                for cfg in selected:
                    try:
                        self._receive_box(cfg)
                        self._send_box(cfg)
                    except Exception:
                        log.exception("mailbox cycle failed for %s", cfg.get("id"))
                try:
                    from app import classify_pending_tickets
                    classified = classify_pending_tickets(int(os.getenv("TICKET_AI_CLASSIFY_BATCH", "5")))
                    if classified:
                        log.info("AI classified %s pending tickets", classified)
                except Exception:
                    log.exception("AI classification batch failed")
            except Exception:
                log.exception("mail worker cycle failed")
            self.stop_event.wait(int(os.getenv("TICKET_MAIL_POLL_SECONDS", "30")))

    def _sync_config(self, configs):
        from app import now
        with self.db() as conn:
            for cfg in configs:
                identity = " ".join(str(cfg.get(key, "")) for key in ("id", "name", "email")).lower()
                mailbox_tag = "NOC-ASN邮箱" if "noc" in identity else cfg.get("mailbox_tag", "未分类")
                if mailbox_tag == "ASN邮箱":
                    mailbox_tag = "NOC-ASN邮箱"
                conn.execute("INSERT INTO mailboxes(id,name,email,color,created_at,enabled,workspace_id,mailbox_tag) VALUES(?,?,?,?,?,1,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,email=excluded.email,color=excluded.color,enabled=1,workspace_id=excluded.workspace_id,mailbox_tag=excluded.mailbox_tag", (cfg["id"], cfg["name"], cfg["email"], cfg.get("color", "#6558d3"), now(), cfg.get("workspace_id", "geekforest"), mailbox_tag))
                conn.execute("INSERT OR IGNORE INTO mailbox_sync(mailbox_id,last_uid,updated_at) VALUES(?,0,?)", (cfg["id"], now()))

    def _password(self, cfg):
        if cfg.get("password"):
            return cfg["password"]
        value = os.getenv(cfg.get("password_env", ""))
        if not value:
            raise RuntimeError(f"missing password environment variable for {cfg['id']}")
        return value

    def _receive_box(self, cfg):
        from app import IncomingMail
        client = imaplib.IMAP4_SSL(cfg["imap_host"], int(cfg.get("imap_port", 993)), ssl_context=ssl.create_default_context())
        try:
            client.login(cfg.get("username", cfg["email"]), self._password(cfg))
            client.select(cfg.get("imap_folder", "INBOX"))
            with self.db() as conn:
                row = conn.execute("SELECT last_uid,backfill_active,backfill_target_uid,last_backfill_at FROM mailbox_sync WHERE mailbox_id=?", (cfg["id"],)).fetchone()
                last_uid = row["last_uid"] if row else 0
                backfill_active = bool(row["backfill_active"]) if row else False
                backfill_target = row["backfill_target_uid"] if row else None
            if last_uid == 0 and os.getenv("TICKET_MAIL_BACKFILL", "0") != "1":
                typ, existing = client.uid("search", None, "ALL")
                uids = existing[0].split() if typ == "OK" and existing else []
                baseline = int(uids[-1]) if uids else 0
                with self.db() as conn:
                    conn.execute("UPDATE mailbox_sync SET last_uid=?,updated_at=datetime('now') WHERE mailbox_id=?", (baseline, cfg["id"]))
                log.info("initialized %s at UID %s without historical backfill", cfg["id"], baseline)
                return
            if last_uid == 0 and os.getenv("TICKET_MAIL_BACKFILL", "0") == "1" and not backfill_active:
                typ, existing = client.uid("search", None, "ALL")
                existing_uids = existing[0].split() if typ == "OK" and existing else []
                backfill_target = int(existing_uids[-1]) if existing_uids else 0
                backfill_active = backfill_target > 0
                with self.db() as conn:
                    conn.execute("UPDATE mailbox_sync SET backfill_active=?,backfill_target_uid=?,last_backfill_at=NULL,updated_at=datetime('now') WHERE mailbox_id=?", (int(backfill_active), backfill_target, cfg["id"]))
                log.info("historical backfill started for %s through UID %s", cfg["id"], backfill_target)
            if backfill_active and row and row["last_backfill_at"]:
                last_run = datetime.fromisoformat(row["last_backfill_at"].replace("Z", "+00:00"))
                interval = int(os.getenv("TICKET_MAIL_BACKFILL_INTERVAL_SECONDS", "3600"))
                if (datetime.now(timezone.utc) - last_run).total_seconds() < interval:
                    return
            typ, data = client.uid("search", None, f"UID {last_uid + 1}:*")
            if typ != "OK": return
            pending_uids = data[0].split()
            if backfill_active:
                pending_uids = [x for x in pending_uids if int(x) <= int(backfill_target or 0)]
                pending_uids = pending_uids[:max(1, int(os.getenv("TICKET_MAIL_BACKFILL_BATCH_PER_MAILBOX", "50")))]
            for raw_uid in pending_uids:
                uid = int(raw_uid)
                typ, fetched = client.uid("fetch", raw_uid, "(RFC822)")
                if typ != "OK" or not fetched or not isinstance(fetched[0], tuple): continue
                msg = email.message_from_bytes(fetched[0][1])
                receipt = _read_receipt(msg)
                if receipt:
                    original_id, detail = receipt
                    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    with self.db() as conn:
                        row = conn.execute("SELECT id,message_id FROM outbox WHERE internet_message_id=?", (original_id,)).fetchone()
                        if row:
                            conn.execute("UPDATE outbox SET opened_at=COALESCE(opened_at,?),delivered_at=COALESCE(delivered_at,?),open_count=open_count+1,updated_at=? WHERE id=?", (ts, ts, ts, row["id"]))
                            if row["message_id"]:
                                conn.execute("UPDATE messages SET delivery_status='read',opened_at=COALESCE(opened_at,?),delivered_at=COALESCE(delivered_at,?),open_count=open_count+1,delivery_error=NULL WHERE id=?", (ts, ts, row["message_id"]))
                    with self.db() as conn:
                        conn.execute("UPDATE mailbox_sync SET last_uid=?,updated_at=datetime('now') WHERE mailbox_id=?", (uid, cfg["id"]))
                    log.info("processed read receipt for %s: %s", original_id, detail)
                    continue
                report = _delivery_report(msg)
                if report:
                    original_id, delivery_status, detail = report
                    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    with self.db() as conn:
                        row = conn.execute("SELECT id,message_id FROM outbox WHERE internet_message_id=?", (original_id,)).fetchone()
                        if row:
                            if delivery_status == "delivered":
                                conn.execute("UPDATE outbox SET delivered_at=COALESCE(delivered_at,?),updated_at=? WHERE id=?", (ts, ts, row["id"]))
                                if row["message_id"]:
                                    conn.execute("UPDATE messages SET delivery_status='delivered',delivered_at=COALESCE(delivered_at,?) WHERE id=?", (ts, row["message_id"]))
                            elif delivery_status == "failed":
                                conn.execute("UPDATE outbox SET status='failed',last_error=?,updated_at=? WHERE id=?", (detail, ts, row["id"]))
                                if row["message_id"]:
                                    conn.execute("UPDATE messages SET delivery_status='failed',failed_at=?,delivery_error=? WHERE id=?", (ts, detail, row["message_id"]))
                    with self.db() as conn:
                        conn.execute("UPDATE mailbox_sync SET last_uid=?,updated_at=datetime('now') WHERE mailbox_id=?", (uid, cfg["id"]))
                    continue
                sender_name, sender_email = parseaddr(_decode(msg.get("From")))
                subject = _decode(msg.get("Subject")) or "（无主题）"
                match = TICKET_RE.search(subject)
                if not sender_email or "@" not in sender_email:
                    log.warning("skip message without valid sender in %s uid %s", cfg["id"], uid)
                    with self.db() as conn:
                        conn.execute("UPDATE mailbox_sync SET last_uid=?,updated_at=datetime('now') WHERE mailbox_id=?", (uid, cfg["id"]))
                    continue
                self.receive(IncomingMail(mailbox_id=cfg["id"], sender_name=(sender_name or sender_email)[:120], sender_email=sender_email, subject=subject[:300], body=(_body(msg) or "（无正文）")[:20_000], in_reply_to_ticket=match.group(1).upper() if match else None, provider_message_id=f"{cfg['id']}:{uid}", internet_message_id=(msg.get("Message-ID") or "")[:1000] or None, references_header=((msg.get("References") or msg.get("In-Reply-To") or "")[:4000] or None), historical=backfill_active))
                with self.db() as conn:
                    conn.execute("UPDATE mailbox_sync SET last_uid=?,updated_at=datetime('now') WHERE mailbox_id=?", (uid, cfg["id"]))
            if backfill_active:
                completed = not pending_uids or int(pending_uids[-1]) >= int(backfill_target or 0)
                with self.db() as conn:
                    conn.execute("UPDATE mailbox_sync SET backfill_active=?,last_backfill_at=datetime('now'),updated_at=datetime('now') WHERE mailbox_id=?", (0 if completed else 1, cfg["id"]))
                if completed:
                    log.info("historical backfill completed for %s at UID %s", cfg["id"], backfill_target)
        finally:
            try: client.logout()
            except Exception: pass

    def _send_box(self, cfg):
        with self.db() as conn:
            rows = list(conn.execute("SELECT o.* FROM outbox o JOIN tickets t ON t.id=o.ticket_id WHERE t.mailbox_id=? AND o.status IN ('queued','failed') AND o.attempts<5 ORDER BY o.created_at LIMIT 20", (cfg["id"],)))
        for row in rows:
            with self.db() as conn:
                changed = conn.execute("UPDATE outbox SET status='sending',attempts=attempts+1,updated_at=datetime('now') WHERE id=? AND status IN ('queued','failed')", (row["id"],)).rowcount
                if changed and row["message_id"]:
                    conn.execute("UPDATE messages SET delivery_status='sending',delivery_error=NULL WHERE id=?", (row["message_id"],))
            if not changed: continue
            message_id = row["internet_message_id"] or make_msgid(domain=cfg["email"].split("@")[-1])
            msg = EmailMessage()
            # The authenticated SMTP identity must be used as the visible sender.
            # Sending a project-domain From through a different relay causes Gmail
            # to reject it when that domain has no SPF/DKIM authorization for the
            # relay. Keep the project mailbox as Reply-To so customer replies still
            # return to the correct inbox.
            smtp_host, smtp_port = cfg["smtp_host"], int(cfg.get("smtp_port", 465))
            smtp_ssl = cfg.get("smtp_ssl", True)
            smtp_user = cfg.get("smtp_username", cfg.get("username", cfg["email"]))
            smtp_password = self._password(cfg)
            sender_candidate = (os.getenv("TICKET_SMTP_FROM") or smtp_user or cfg.get("username") or cfg["email"]).strip()
            sender = sender_candidate if "@" in sender_candidate else cfg["email"]
            to_emails = [x.strip() for x in (row["to_emails"] or row["to_email"] or "").split(",") if x.strip()]
            msg["From"], msg["To"], msg["Subject"], msg["Date"], msg["Message-ID"] = sender, ", ".join(to_emails), row["subject"], formatdate(localtime=False), message_id
            # Ask standards-compliant clients for an MDN. Recipients may decline
            # or their provider may suppress it, so absence is never treated as unread.
            msg["Disposition-Notification-To"] = sender
            msg["Return-Receipt-To"] = sender
            cc = [x.strip() for x in (row["cc_emails"] or "").split(",") if x.strip()]
            bcc = [x.strip() for x in (row["bcc_emails"] or "").split(",") if x.strip()]
            if cc: msg["Cc"] = ", ".join(cc)
            if cfg["email"].lower() != sender.lower():
                msg["Reply-To"] = cfg["email"]
            if row["in_reply_to"]: msg["In-Reply-To"] = row["in_reply_to"]
            if row["references_header"]: msg["References"] = row["references_header"]
            msg.set_content(row["body"])
            if row["tracking_token"] and os.getenv("TICKET_EMAIL_OPEN_TRACKING", "1") == "1":
                public_url = os.getenv("TICKET_PUBLIC_URL", "https://ticket.geekforest.ai").rstrip("/")
                pixel = f'{public_url}/api/email-events/open/{row["tracking_token"]}.gif'
                safe_body = html.escape(row["body"]).replace("\n", "<br>\n")
                msg.add_alternative(f'<html><body><div>{safe_body}</div><img src="{pixel}" width="1" height="1" alt="" style="display:block;width:1px;height:1px;border:0"></body></html>', subtype="html")
            try:
                if smtp_ssl:
                    smtp = smtplib.SMTP_SSL(smtp_host, smtp_port, context=ssl.create_default_context())
                else:
                    smtp = smtplib.SMTP(smtp_host, smtp_port); smtp.starttls(context=ssl.create_default_context())
                try:
                    smtp.login(smtp_user, smtp_password)
                    send_kwargs = {"from_addr": sender, "to_addrs": [*to_emails, *cc, *bcc]}
                    if smtp.has_extn("dsn"):
                        send_kwargs["mail_options"] = ("RET=HDRS", f"ENVID={row['id']}")
                        send_kwargs["rcpt_options"] = ("NOTIFY=SUCCESS,FAILURE,DELAY",)
                    smtp.send_message(msg, **send_kwargs)
                finally: smtp.quit()
                sent_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                with self.db() as conn:
                    conn.execute("UPDATE outbox SET status='sent',internet_message_id=?,last_error=NULL,updated_at=? WHERE id=?", (message_id, sent_at, row["id"]))
                    if row["message_id"]:
                        conn.execute("UPDATE messages SET delivery_status='sent',sent_at=?,internet_message_id=?,delivery_error=NULL WHERE id=?", (sent_at, message_id, row["message_id"]))
            except Exception as exc:
                log.warning("send failed for %s: %s", row["id"], exc)
                failed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                error_text = str(exc)[:500]
                with self.db() as conn:
                    conn.execute("UPDATE outbox SET status='failed',last_error=?,updated_at=? WHERE id=?", (error_text, failed_at, row["id"]))
                    if row["message_id"]:
                        conn.execute("UPDATE messages SET delivery_status='failed',failed_at=?,delivery_error=? WHERE id=?", (failed_at, error_text, row["message_id"]))
