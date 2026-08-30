#!/usr/bin/env python3
"""Copy legacy project mailboxes to Stalwart without deleting source mail."""

from __future__ import annotations

import argparse
import imaplib
import os
import re
import sqlite3
import ssl
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mail_worker import load_configs  # noqa: E402


LIST_RE = re.compile(rb"^\((?P<attrs>[^)]*)\)\s+(?P<delimiter>\"(?:[^\"\\]|\\.)*\"|NIL)\s+(?P<name>.+)$")


def folder_name(row: bytes) -> bytes | None:
    match = LIST_RE.match(row)
    if not match or b"\\Noselect" in match.group("attrs"):
        return None
    value = match.group("name").strip()
    if value.startswith(b'"') and value.endswith(b'"'):
        value = value[1:-1].replace(b'\\"', b'"').replace(b"\\\\", b"\\")
    return value


def source_context(allow_expired: bool) -> ssl.SSLContext:
    if not allow_expired:
        return ssl.create_default_context()
    return ssl._create_unverified_context()  # noqa: SLF001 - fixed legacy host during migration only


def init_state(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS copied_messages(
                mailbox_id TEXT NOT NULL,
                folder_hex TEXT NOT NULL,
                source_uid INTEGER NOT NULL,
                copied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(mailbox_id, folder_hex, source_uid)
            )"""
        )


def already_copied(path: Path, mailbox_id: str, folder: bytes, uid: int) -> bool:
    with sqlite3.connect(path) as conn:
        return conn.execute(
            "SELECT 1 FROM copied_messages WHERE mailbox_id=? AND folder_hex=? AND source_uid=?",
            (mailbox_id, folder.hex(), uid),
        ).fetchone() is not None


def mark_copied(path: Path, mailbox_id: str, folder: bytes, uid: int) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO copied_messages(mailbox_id,folder_hex,source_uid) VALUES(?,?,?)",
            (mailbox_id, folder.hex(), uid),
        )


def copy_mailbox(cfg: dict, args: argparse.Namespace) -> dict:
    result = {"id": cfg["id"], "copied": 0, "skipped": 0, "failed": 0, "folders": 0, "error": ""}
    source = target = None
    try:
        source = imaplib.IMAP4_SSL(
            args.source_host,
            args.source_port,
            ssl_context=source_context(args.allow_expired_source_certificate),
            timeout=args.timeout,
        )
        target = imaplib.IMAP4_SSL(
            args.target_host,
            args.target_port,
            ssl_context=ssl.create_default_context(),
            timeout=args.timeout,
        )
        source.login(cfg["email"], cfg["password"])
        target.login(cfg["email"], cfg["password"])
        status, rows = source.list()
        if status != "OK":
            raise RuntimeError("source LIST failed")
        for row in rows or []:
            folder = folder_name(row)
            if not folder:
                continue
            result["folders"] += 1
            if folder.upper() != b"INBOX":
                target.create(folder)
            status, _ = source.select(folder, readonly=True)
            if status != "OK":
                result["failed"] += 1
                continue
            status, data = source.uid("search", None, "ALL")
            if status != "OK":
                result["failed"] += 1
                continue
            for raw_uid in (data[0].split() if data and data[0] else []):
                uid = int(raw_uid)
                if already_copied(args.state, cfg["id"], folder, uid):
                    result["skipped"] += 1
                    continue
                status, fetched = source.uid("fetch", raw_uid, "(RFC822 FLAGS INTERNALDATE)")
                item = fetched[0] if fetched else None
                if status != "OK" or not isinstance(item, tuple):
                    result["failed"] += 1
                    continue
                meta, message = item
                flags_match = re.search(rb"FLAGS\s+\(([^)]*)\)", meta, re.I)
                date_match = re.search(rb'INTERNALDATE\s+("[^"]+")', meta, re.I)
                flags = flags_match.group(1).decode("ascii", "ignore") if flags_match else None
                internal_date = date_match.group(1).decode("ascii", "ignore") if date_match else None
                status, _ = target.append(folder, flags, internal_date, message)
                if status == "OK":
                    mark_copied(args.state, cfg["id"], folder, uid)
                    result["copied"] += 1
                else:
                    result["failed"] += 1
        return result
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        return result
    finally:
        for client in (source, target):
            if client:
                try:
                    client.logout()
                except Exception:
                    pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-host", default="mail.willech.com")
    parser.add_argument("--source-port", type=int, default=993)
    parser.add_argument("--target-host", default="mail2.willech.com")
    parser.add_argument("--target-port", type=int, default=993)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--allow-expired-source-certificate", action="store_true")
    args = parser.parse_args()
    args.state.parent.mkdir(parents=True, exist_ok=True)
    init_state(args.state)
    configs = [
        cfg
        for cfg in load_configs(ROOT)
        if cfg.get("smtp_host") == args.source_host and cfg["id"].startswith("project-")
    ]
    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(copy_mailbox, cfg, args) for cfg in configs]
        for future in as_completed(futures):
            row = future.result()
            results.append(row)
            print(
                f"{row['id']} folders={row['folders']} copied={row['copied']} "
                f"skipped={row['skipped']} failed={row['failed']} error={row['error'] or '-'}",
                flush=True,
            )
    copied = sum(row["copied"] for row in results)
    skipped = sum(row["skipped"] for row in results)
    failed = sum(row["failed"] for row in results)
    errors = sum(bool(row["error"]) for row in results)
    print(f"SUMMARY accounts={len(results)} copied={copied} skipped={skipped} failed={failed} account_errors={errors}")
    return 1 if failed or errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
