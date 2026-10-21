import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

import app as ticket_app


class MailboxAccessTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_path = ticket_app.DB_PATH
        ticket_app.DB_PATH = Path(self.tempdir.name) / "tickets.db"
        ticket_app.init_db()
        with ticket_app.db() as conn:
            ts = ticket_app.now()
            conn.execute(
                "INSERT INTO users(id,username,display_name,password_hash,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("restricted-user", "ez_user", "EZ", "unused", ts, ts),
            )
            conn.execute(
                "INSERT INTO workspace_memberships(user_id,workspace_id,role,created_at) VALUES(?,?,?,?)",
                ("restricted-user", "eddy-personal", "member", ts),
            )
            conn.executemany(
                "INSERT INTO mailboxes(id,name,email,color,created_at,workspace_id) VALUES(?,?,?,?,?,?)",
                [
                    ("allowed-box", "Oliver", "oliver@woyonexus.com", "#000", ts, "eddy-personal"),
                    ("blocked-box", "Other", "other@woyonexus.com", "#111", ts, "eddy-personal"),
                ],
            )
            conn.executemany(
                """INSERT INTO tickets
                    (id,subject,customer_name,customer_email,mailbox_id,status,priority,assignee,created_at,updated_at)
                    VALUES(?,?,?,?,?,'open','normal','未分配',?,?)""",
                [
                    ("TKT-1", "Allowed", "A", "a@example.test", "allowed-box", ts, ts),
                    ("TKT-2", "Blocked", "B", "b@example.test", "blocked-box", ts, ts),
                ],
            )
            conn.execute(
                "INSERT INTO user_mailbox_access(user_id,mailbox_id,created_at) VALUES(?,?,?)",
                ("restricted-user", "allowed-box", ts),
            )
        self.request = SimpleNamespace(
            cookies={"ticket_session": ticket_app.session_token("restricted-user", "eddy-personal", int(time.time()) + 600)},
            client=SimpleNamespace(host="test"),
        )

    def tearDown(self):
        ticket_app.DB_PATH = self.original_db_path
        self.tempdir.cleanup()

    def test_list_and_session_are_scoped_to_granted_mailbox(self):
        session = ticket_app.session_info(self.request)
        self.assertEqual("allowed-box", session["default_mailbox_id"])

        result = ticket_app.list_tickets(self.request)
        self.assertEqual(["TKT-1"], [ticket["id"] for ticket in result["tickets"]])
        self.assertEqual(["allowed-box"], [mailbox["id"] for mailbox in result["mailboxes"]])
        self.assertEqual({"open": 1}, result["counts"])

    def test_direct_ticket_and_mutation_paths_cannot_cross_mailbox_scope(self):
        with self.assertRaises(HTTPException) as detail:
            ticket_app.get_ticket("TKT-2", self.request)
        self.assertEqual(404, detail.exception.status_code)

        with self.assertRaises(HTTPException) as compose:
            ticket_app.compose_mail(
                ticket_app.ComposeMailIn(
                    mailbox_id="blocked-box",
                    to_email="customer@example.test",
                    subject="No access",
                    body="This must be rejected.",
                ),
                self.request,
            )
        self.assertEqual(404, compose.exception.status_code)

        with self.assertRaises(HTTPException) as update:
            ticket_app.update_ticket("TKT-2", {"status": "resolved"}, self.request)
        self.assertEqual(404, update.exception.status_code)

    def test_unconfigured_user_keeps_workspace_wide_behavior(self):
        with ticket_app.db() as conn:
            ts = ticket_app.now()
            conn.execute(
                "INSERT INTO users(id,username,display_name,password_hash,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("legacy-user", "legacy", "Legacy", "unused", ts, ts),
            )
            conn.execute(
                "INSERT INTO workspace_memberships(user_id,workspace_id,role,created_at) VALUES(?,?,?,?)",
                ("legacy-user", "eddy-personal", "member", ts),
            )
        request = SimpleNamespace(
            cookies={"ticket_session": ticket_app.session_token("legacy-user", "eddy-personal", int(time.time()) + 600)},
            client=SimpleNamespace(host="test"),
        )
        result = ticket_app.list_tickets(request)
        self.assertEqual({"TKT-1", "TKT-2"}, {ticket["id"] for ticket in result["tickets"]})


if __name__ == "__main__":
    unittest.main()
