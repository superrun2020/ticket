import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import app as ticket_app


class InboundCursorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_path = ticket_app.DB_PATH
        ticket_app.DB_PATH = Path(self.tempdir.name) / "tickets.db"
        ticket_app.init_db()
        with ticket_app.db() as conn:
            now = ticket_app.now()
            conn.execute("INSERT INTO users(id,username,display_name,password_hash,is_admin,created_at,updated_at) VALUES('u1','tester','Tester','unused',0,?,?)", (now, now))
            conn.execute("INSERT INTO workspace_memberships(user_id,workspace_id,role,created_at) VALUES('u1','geekforest','member',?)", (now,))
            conn.execute("INSERT INTO mailboxes(id,name,email,color,created_at,workspace_id) VALUES('other-box','Other','other@example.test','#000',?,'eddy-personal')", (now,))
            conn.execute("INSERT INTO tickets(id,subject,customer_name,customer_email,mailbox_id,status,priority,assignee,created_at,updated_at) VALUES('TKT-9001','Existing','A','a@example.test','support','open','normal','未分配',?,?)", (now, now))
            conn.execute("INSERT INTO tickets(id,subject,customer_name,customer_email,mailbox_id,status,priority,assignee,created_at,updated_at) VALUES('TKT-9002','Other workspace','B','b@example.test','other-box','open','normal','未分配',?,?)", (now, now))
            conn.execute("INSERT INTO messages(id,ticket_id,direction,sender_name,sender_email,body,created_at,is_read) VALUES('old','TKT-9001','inbound','A','a@example.test','old',?,0)", (now,))
        self.request = SimpleNamespace(cookies={"ticket_session": ticket_app.session_token('u1', 'geekforest', int(__import__('time').time()) + 600)})

    def tearDown(self):
        ticket_app.DB_PATH = self.original_db_path
        self.tempdir.cleanup()

    def test_cursor_baselines_then_returns_only_new_workspace_inbound_without_read_mutation(self):
        baseline = ticket_app.inbound_message_cursor(self.request)
        self.assertEqual([], baseline["messages"])

        with ticket_app.db() as conn:
            now = ticket_app.now()
            conn.execute("INSERT INTO messages(id,ticket_id,direction,sender_name,sender_email,body,created_at,is_read) VALUES('reply','TKT-9001','inbound','A','a@example.test','new reply',?,0)", (now,))
            conn.execute("INSERT INTO messages(id,ticket_id,direction,sender_name,sender_email,body,created_at,is_read) VALUES('sent','TKT-9001','outbound','Tester','support@example.test','sent',?,1)", (now,))
            conn.execute("INSERT INTO messages(id,ticket_id,direction,sender_name,sender_email,body,created_at,is_read) VALUES('foreign','TKT-9002','inbound','B','b@example.test','foreign',?,0)", (now,))

        result = ticket_app.inbound_message_cursor(self.request, since=baseline["cursor"])
        self.assertEqual(["reply"], [message["id"] for message in result["messages"]])
        self.assertEqual("Existing", result["messages"][0]["subject"])
        with ticket_app.db() as conn:
            self.assertEqual(0, conn.execute("SELECT is_read FROM messages WHERE id='reply'").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
