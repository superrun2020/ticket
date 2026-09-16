import asyncio
import json
import tempfile
import time
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

import app as ticket_app
from mail_worker import MailWorker, normalized_header_addresses


class ReplyAllTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_path = ticket_app.DB_PATH
        self.original_attachment_dir = ticket_app.ATTACHMENT_DIR
        self.original_root = ticket_app.ROOT
        ticket_app.DB_PATH = Path(self.tempdir.name) / "tickets.db"
        ticket_app.ATTACHMENT_DIR = Path(self.tempdir.name) / "attachments"
        ticket_app.init_db()
        ticket_app.ROOT = Path(self.tempdir.name)
        with ticket_app.db() as conn:
            ts = ticket_app.now()
            conn.execute("INSERT INTO users(id,username,display_name,password_hash,is_admin,created_at,updated_at) VALUES('reply-user','reply','Agent','unused',0,?,?)", (ts, ts))
            conn.execute("INSERT INTO workspace_memberships(user_id,workspace_id,role,created_at) VALUES('reply-user','geekforest','member',?)", (ts,))
            conn.execute("INSERT INTO mailboxes(id,name,email,color,created_at,workspace_id) VALUES('foreign-box','Foreign','foreign@example.test','#000',?,'eddy-personal')", (ts,))
            conn.execute("INSERT INTO tickets(id,subject,customer_name,customer_email,mailbox_id,status,priority,assignee,created_at,updated_at) VALUES('TKT-9100','Reply all','Alice','alice@example.test','support','open','normal','Agent',?,?)", (ts, ts))
            conn.execute("INSERT INTO tickets(id,subject,customer_name,customer_email,mailbox_id,status,priority,assignee,created_at,updated_at) VALUES('TKT-9200','Foreign','Mallory','mallory@example.test','foreign-box','open','normal','Agent',?,?)", (ts, ts))
            conn.execute("""INSERT INTO messages(id,ticket_id,direction,sender_name,sender_email,body,created_at,internet_message_id,references_header,inbound_to_emails,inbound_cc_emails,inbound_reply_to_emails)
                VALUES('parent','TKT-9100','inbound','Alice','alice@example.test','hello',?,'<parent@example.test>','<root@example.test>','support@postpilot.io,TEAM@example.test','team@example.test,bob@example.test','reply@example.test')""", (ts,))
            conn.execute("INSERT INTO messages(id,ticket_id,direction,sender_name,sender_email,body,created_at,internet_message_id) VALUES('foreign-parent','TKT-9200','inbound','Mallory','mallory@example.test','x',?,'<foreign@example.test>')", (ts,))
        self.request = SimpleNamespace(cookies={"ticket_session": ticket_app.session_token('reply-user', 'geekforest', int(time.time()) + 600)}, client=SimpleNamespace(host='test'))

    async def asgi_request(self, method, path, body=b'', content_type=None):
        headers = [(b'cookie', f"ticket_session={self.request.cookies['ticket_session']}".encode())]
        if content_type:
            headers.append((b'content-type', content_type.encode()))
        sent = []
        delivered = False
        async def receive():
            nonlocal delivered
            if delivered:
                return {'type': 'http.disconnect'}
            delivered = True
            return {'type': 'http.request', 'body': body, 'more_body': False}
        async def send(message):
            sent.append(message)
        await ticket_app.app({'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':method,
            'scheme':'http','path':path,'raw_path':path.encode(),'query_string':b'','headers':headers,
            'client':('test',123),'server':('test',80),'root_path':''}, receive, send)
        status = next(item['status'] for item in sent if item['type'] == 'http.response.start')
        payload = b''.join(item.get('body', b'') for item in sent if item['type'] == 'http.response.body')
        return status, json.loads(payload or b'{}')

    def multipart(self, fields):
        boundary = 'reply-all-regression'
        parts = []
        for name, value in fields:
            parts.extend([f'--{boundary}\r\n'.encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), str(value).encode(), b'\r\n'])
        parts.append(f'--{boundary}--\r\n'.encode())
        return b''.join(parts), f'multipart/form-data; boundary={boundary}'

    def tearDown(self):
        ticket_app.DB_PATH = self.original_db_path
        ticket_app.ATTACHMENT_DIR = self.original_attachment_dir
        ticket_app.ROOT = self.original_root
        self.tempdir.cleanup()

    def test_header_parser_tolerates_malformed_and_keeps_valid_addresses(self):
        self.assertEqual(['a@example.test', 'b@example.test'], normalized_header_addresses('A <a@example.test>, broken, B <B@example.test>, bad@'))

    def test_preview_reply_all_prefers_reply_to_and_dedupes_self_and_cc(self):
        with ticket_app.db() as conn:
            conn.execute("UPDATE messages SET inbound_reply_to_emails='reply@example.test,SECOND@example.test', inbound_to_emails='support@postpilot.io,second@example.test,TEAM@example.test' WHERE id='parent'")
        preview = ticket_app.reply_recipient_preview('TKT-9100', self.request, parent_message_id='parent')
        self.assertEqual('parent', preview['parent_message_id'])
        self.assertEqual(['reply@example.test', 'second@example.test'], preview['reply']['to'])
        self.assertEqual(['reply@example.test', 'second@example.test', 'team@example.test'], preview['reply_all']['to'])
        self.assertEqual(['bob@example.test'], preview['reply_all']['cc'])
        self.assertFalse(preview['historical_metadata_unavailable'])

    def test_old_metadata_and_no_inbound_fall_back_without_claiming_reconstruction(self):
        with ticket_app.db() as conn:
            conn.execute("UPDATE messages SET inbound_to_emails=NULL,inbound_cc_emails=NULL,inbound_reply_to_emails=NULL WHERE id='parent'")
        preview = ticket_app.reply_recipient_preview('TKT-9100', self.request, parent_message_id='parent')
        self.assertEqual(['alice@example.test'], preview['reply_all']['to'])
        self.assertTrue(preview['historical_metadata_unavailable'])
        with ticket_app.db() as conn:
            conn.execute("DELETE FROM messages WHERE ticket_id='TKT-9100'")
        preview = ticket_app.reply_recipient_preview('TKT-9100', self.request)
        self.assertIsNone(preview['parent_message_id'])
        self.assertEqual(['alice@example.test'], preview['reply']['to'])

    def test_workspace_and_parent_scope_are_enforced(self):
        with self.assertRaises(HTTPException) as foreign:
            ticket_app.reply_recipient_preview('TKT-9200', self.request)
        self.assertEqual(404, foreign.exception.status_code)
        with self.assertRaises(HTTPException) as wrong_parent:
            ticket_app.reply_recipient_preview('TKT-9100', self.request, parent_message_id='foreign-parent')
        self.assertEqual(422, wrong_parent.exception.status_code)

    def test_json_reply_persists_displayed_recipients_and_anchor_and_rejects_injection(self):
        payload = ticket_app.ReplyIn(body='answer', to=['reply@example.test', 'TEAM@example.test'], cc=['bob@example.test'], parent_message_id='parent')
        ticket_app.reply('TKT-9100', payload, self.request)
        with ticket_app.db() as conn:
            row = conn.execute("SELECT * FROM outbox WHERE message_id IS NOT NULL ORDER BY created_at DESC LIMIT 1").fetchone()
        self.assertEqual('reply@example.test,team@example.test', row['to_emails'])
        self.assertEqual('bob@example.test', row['cc_emails'])
        self.assertEqual('<parent@example.test>', row['in_reply_to'])
        self.assertEqual('<root@example.test> <parent@example.test>', row['references_header'])
        with self.assertRaises(HTTPException):
            ticket_app.reply('TKT-9100', ticket_app.ReplyIn(body='bad', to=['ok@example.test\r\nBcc:x@example.test'], parent_message_id='parent'), self.request)

    def test_legacy_plain_reply_payload_remains_compatible_and_anchor_stays_stable(self):
        with ticket_app.db() as conn:
            later = ticket_app.now()
            conn.execute("INSERT INTO messages(id,ticket_id,direction,sender_name,sender_email,body,created_at,internet_message_id) VALUES('later','TKT-9100','inbound','Later','later@example.test','later',?,'<later@example.test>')", (later,))
        result = ticket_app.reply('TKT-9100', ticket_app.ReplyIn(body='legacy'), self.request)
        with ticket_app.db() as conn:
            legacy = conn.execute("SELECT * FROM outbox WHERE message_id=?", (result['message_id'],)).fetchone()
        self.assertEqual('later@example.test', legacy['to_emails'])
        anchored = ticket_app.reply('TKT-9100', ticket_app.ReplyIn(body='anchored', to=['reply@example.test'], parent_message_id='parent'), self.request)
        with ticket_app.db() as conn:
            row = conn.execute("SELECT * FROM outbox WHERE message_id=?", (anchored['message_id'],)).fetchone()
        self.assertEqual('<parent@example.test>', row['in_reply_to'])

    def test_explicit_null_json_and_empty_multipart_freeze_no_parent_while_omission_is_legacy(self):
        with ticket_app.db() as conn:
            conn.execute("DELETE FROM messages WHERE ticket_id='TKT-9100'")
        preview = ticket_app.reply_recipient_preview('TKT-9100', self.request)
        self.assertIsNone(preview['parent_message_id'])
        with ticket_app.db() as conn:
            ts = ticket_app.now()
            conn.execute("INSERT INTO messages(id,ticket_id,direction,sender_name,sender_email,body,created_at,internet_message_id) VALUES('later','TKT-9100','inbound','Later','later@example.test','later',?,'<later@example.test>')", (ts,))
        frozen = ticket_app.reply('TKT-9100', ticket_app.ReplyIn(body='frozen', parent_message_id=preview['parent_message_id']), self.request)
        body, content_type = self.multipart([('body','multipart frozen'),('to','alice@example.test'),('parent_message_id','')])
        status, multipart = asyncio.run(self.asgi_request('POST', '/api/tickets/TKT-9100/reply-with-attachments', body, content_type))
        self.assertEqual(200, status, multipart)
        legacy = ticket_app.reply('TKT-9100', ticket_app.ReplyIn(body='legacy'), self.request)
        with ticket_app.db() as conn:
            rows = {row['message_id']: row for row in conn.execute("SELECT * FROM outbox WHERE message_id IN (?,?,?)", (frozen['message_id'], multipart['message_id'], legacy['message_id']))}
        self.assertIsNone(rows[frozen['message_id']]['in_reply_to'])
        self.assertIsNone(rows[multipart['message_id']]['in_reply_to'])
        self.assertEqual('<later@example.test>', rows[legacy['message_id']]['in_reply_to'])

    def test_actual_asgi_multipart_explicit_empty_to_is_not_legacy_fallback(self):
        body, content_type = self.multipart([('body','must not send'),('to',''),('parent_message_id','parent')])
        status, payload = asyncio.run(self.asgi_request('POST', '/api/tickets/TKT-9100/reply-with-attachments', body, content_type))
        self.assertEqual(422, status)
        self.assertEqual('RECIPIENT_REQUIRED', payload['detail']['error'])
        with ticket_app.db() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM outbox WHERE body='must not send'").fetchone())

    def test_ticket_messages_include_saved_outbox_to_cc_without_bcc(self):
        sent = ticket_app.reply('TKT-9100', ticket_app.ReplyIn(body='visible', to=['one@example.test'], cc=['two@example.test'], parent_message_id='parent'), self.request)
        with ticket_app.db() as conn:
            conn.execute("UPDATE outbox SET bcc_emails='hidden@example.test' WHERE message_id=?", (sent['message_id'],))
        detail = ticket_app.get_ticket('TKT-9100', self.request)
        message = next(item for item in detail['messages'] if item['id'] == sent['message_id'])
        self.assertEqual(['one@example.test'], message['to'])
        self.assertEqual(['two@example.test'], message['cc'])
        self.assertNotIn('bcc', message)

    def test_strict_recipients_rejects_invalid_domains_and_whitespace_but_keeps_valid_forms(self):
        for invalid in ['a@example..com', 'a@-example.com', 'a@example-.com', ' a@example.com', 'a@example.com ', 'a @example.com']:
            with self.subTest(invalid=invalid), self.assertRaises(HTTPException):
                ticket_app.strict_recipients([invalid])
        self.assertEqual(['user+tag@mail.sub.example.com', 'name@xn--bcher-kva.example'],
            ticket_app.strict_recipients(['user+tag@mail.sub.example.com', 'name@xn--bcher-kva.example']))

    def test_incoming_integration_normalizes_and_persists_headers_without_bcc(self):
        result = ticket_app.receive_mail(ticket_app.IncomingMail(mailbox_id='support', sender_name='Alice', sender_email='alice@example.com',
            subject='Re: [TKT-9100] Reply all', body='incoming', in_reply_to_ticket='TKT-9100', provider_message_id='integration:1',
            to_emails=['Support <SUPPORT@POSTPILOT.IO>', 'bad@'], cc_emails=['Bob <bob@example.com>', 'broken'],
            reply_to_emails=['Replies <reply@example.com>']))
        with ticket_app.db() as conn:
            row = conn.execute("SELECT * FROM messages WHERE provider_message_id='integration:1'").fetchone()
            columns = {item[1] for item in conn.execute('PRAGMA table_info(messages)')}
        self.assertEqual('support@postpilot.io', row['inbound_to_emails'])
        self.assertEqual('bob@example.com', row['inbound_cc_emails'])
        self.assertEqual('reply@example.com', row['inbound_reply_to_emails'])
        self.assertFalse(any('bcc' in name.lower() for name in columns if name.startswith('inbound_')))

    def test_multipart_reply_keeps_attachment_and_recipients(self):
        upload = SimpleNamespace(filename='note.txt', content_type='text/plain', read=lambda: asyncio.sleep(0, result=b'note'))
        result = asyncio.run(ticket_app.reply_with_attachments('TKT-9100', self.request, body='attached', to='reply@example.test', cc='bob@example.test', parent_message_id='parent', files=[upload]))
        self.assertEqual('note.txt', result['attachments'][0]['filename'])
        with ticket_app.db() as conn:
            row = conn.execute("SELECT * FROM outbox WHERE message_id=?", (result['message_id'],)).fetchone()
        self.assertEqual('reply@example.test', row['to_emails'])
        self.assertEqual('bob@example.test', row['cc_emails'])

    def test_fake_smtp_observes_mime_to_cc_envelope_and_no_bcc_header(self):
        with ticket_app.db() as conn:
            ts = ticket_app.now()
            conn.execute("INSERT INTO messages(id,ticket_id,direction,sender_name,sender_email,body,created_at,delivery_status) VALUES('mime-msg','TKT-9100','outbound','Agent','support@postpilot.io','body',?,'queued')", (ts,))
            conn.execute("INSERT INTO outbox(id,ticket_id,to_email,subject,body,status,created_at,updated_at,to_emails,cc_emails,bcc_emails,message_id) VALUES('mime-out','TKT-9100','one@example.test','subject','body','queued',?,?,'one@example.test','two@example.test','hidden@example.test','mime-msg')", (ts, ts))
        observed = {}
        class FakeSMTP:
            def __init__(self, *args, **kwargs): pass
            def login(self, *args): pass
            def has_extn(self, name): return False
            def send_message(self, message, **kwargs): observed.update(message=message, kwargs=kwargs)
            def quit(self): pass
        worker = MailWorker(ticket_app.ROOT, ticket_app.db, ticket_app.receive_mail)
        cfg = {'id':'support','email':'support@postpilot.io','smtp_host':'fake','smtp_port':465,'smtp_ssl':True,'smtp_username':'support@postpilot.io','password':'unused'}
        with patch('mail_worker.smtplib.SMTP_SSL', FakeSMTP):
            worker._send_box(cfg)
        msg = observed['message']
        self.assertEqual('one@example.test', msg['To'])
        self.assertEqual('two@example.test', msg['Cc'])
        self.assertIsNone(msg['Bcc'])
        self.assertEqual(['one@example.test', 'two@example.test', 'hidden@example.test'], observed['kwargs']['to_addrs'])


if __name__ == '__main__':
    unittest.main()
