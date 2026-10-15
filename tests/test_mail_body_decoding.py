"""Synthetic MIME regressions; never use customer mail as fixtures."""
import base64
import email
import unittest
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from mail_worker import MailWorker, _attachments, _body


def text_part(text, *, codec="utf-8", declared=None, kind="plain"):
    return email.message_from_bytes(
        (f'Content-Type: text/{kind}; charset="{declared or codec}"\r\n'
         'Content-Transfer-Encoding: base64\r\n\r\n').encode()
        + base64.b64encode(text.encode(codec))
    )


class MailCharsetTests(unittest.TestCase):
    def test_unknown_charset_html_uses_utf8_then_replacement(self):
        for text, codec, expected in [('Hello', 'utf-8', 'Hello'),
                                      ('中文 😀', 'utf-8', '中文 😀'),
                                      ('café', 'iso-8859-1', 'caf\ufffd')]:
            with self.subTest(codec=codec, text=text):
                msg = EmailMessage()
                msg.make_alternative()
                msg.attach(text_part(f'<p>{text}</p>', codec=codec,
                                     declared='x-unknown', kind='html'))
                original = msg.get_payload()[0].get_payload(decode=True)
                self.assertEqual(_body(msg), expected)
                self.assertEqual(msg.get_payload()[0].get_payload(decode=True), original)

    def test_unknown_charset_does_not_block_ingest_cursor_or_send_cycle(self):
        msg = EmailMessage()
        msg['From'] = 'sender@example.invalid'
        msg.make_alternative()
        msg.attach(text_part('<p>Hello</p>', declared='x-unknown', kind='html'))
        next_msg = text_part('Next notice')
        next_msg['From'] = 'sender@example.invalid'
        client = MagicMock()
        client.uid.side_effect = [('OK', [b'2 3']),
                                 ('OK', [(b'2', msg.as_bytes())]),
                                 ('OK', [(b'3', next_msg.as_bytes())])]
        db = MagicMock()
        conn = db.return_value.__enter__.return_value
        conn.execute.return_value.fetchone.return_value = {
            'last_uid': 1, 'backfill_active': 0, 'backfill_target_uid': None}
        receive = Mock()
        worker = MailWorker(Path('.'), db, receive)
        worker.stop_event = Mock()
        worker.stop_event.is_set.side_effect = [False, True]
        cfg = {'id': 'synthetic', 'email': 'box@example.invalid',
               'imap_host': 'imap.example.invalid', 'password': 'synthetic'}
        fake_app = SimpleNamespace(IncomingMail=SimpleNamespace,
                                   classify_pending_tickets=Mock(return_value=0),
                                   process_admob_policy_alerts=Mock(return_value=0))
        with patch.dict('sys.modules', {'app': fake_app}), \
                patch('mail_worker.imaplib.IMAP4_SSL', return_value=client), \
                patch('mail_worker.load_configs', return_value=[cfg]), \
                patch.object(worker, '_sync_project_mail_service_if_due'), \
                patch.object(worker, '_sync_config'), \
                patch.object(worker, '_send_box') as send:
            worker._loop()
        self.assertEqual([call.args[0].body for call in receive.call_args_list],
                         ['Hello', 'Next notice'])
        updates = [call.args[1] for call in conn.execute.call_args_list
                   if call.args[0].startswith('UPDATE mailbox_sync SET last_uid=')]
        self.assertEqual(updates, [(2, 'synthetic'), (3, 'synthetic')])
        send.assert_called_once_with(cfg)
        client.logout.assert_called_once()

    def test_gbk_extension_bytes_under_gb2312_label_are_not_destroyed(self):
        text = "合成測試：尊敬的客戶，請查閱通知。Reference ABC-123"
        msg = text_part(text, codec="gbk", declared="gb2312")
        self.assertEqual(_body(msg), text)
    def test_multipart_html_body_is_plain_text_not_active_html(self):
        msg = EmailMessage()
        msg.make_mixed()
        msg.attach(text_part('<html><head><style>secret-style</style></head>'
                             '<body><p>合成通知 &amp; notice</p><script>secret-script</script>'
                             '<p>Second line<img src="https://invalid.example/tracker"></p></body></html>', kind="html"))
        self.assertEqual(_body(msg), '合成通知 & notice\nSecond line')
    def test_attached_multipart_subtree_is_not_a_body_alternative(self):
        msg = EmailMessage()
        msg.make_mixed()
        attached = EmailMessage()
        attached.make_mixed()
        attached['Content-Disposition'] = 'attachment; filename="forward.eml"'
        attached.attach(text_part('Private attachment content'))
        msg.attach(attached)
        msg.attach(text_part('<p>Actual body</p>', kind='html'))
        self.assertEqual(_body(msg), 'Actual body')

    def test_named_html_without_disposition_is_not_promoted_to_body(self):
        part = text_part('<p>Secure wrapper</p>', kind='html')
        part.set_param('name', 'secure.html')
        self.assertEqual(_body(part), '')
    def test_working_declared_encodings_keep_their_exact_text(self):
        for codec, text in [('utf-8', '中文 😀'), ('gb2312', '简体中文'),
                            ('big5', '繁體中文'), ('gbk', '測試'),
                            ('iso-8859-1', 'café'), ('windows-1252', '“notice”')]:
            with self.subTest(codec=codec):
                self.assertEqual(_body(text_part(text, codec=codec)), text)

    def test_gb2312_alias_uses_the_same_narrow_extension(self):
        self.assertEqual(_body(text_part('測試', codec='gbk', declared='chinese')), '測試')

    def test_invalid_utf8_does_not_guess_a_legacy_codec(self):
        msg = email.message_from_bytes(b'Content-Type: text/plain; charset=utf-8\r\n\r\n\xff')
        self.assertEqual(_body(msg), '\ufffd')

    def test_truncated_gb2312_retains_existing_replacement_behavior(self):
        msg = email.message_from_bytes(b'Content-Type: text/plain; charset=gb2312\r\n\r\n\x81')
        self.assertEqual(_body(msg), '\ufffd')

    def test_html_only_single_part_is_inert_text(self):
        self.assertEqual(_body(text_part('<p>Hello<br>World</p>', kind='html')), 'Hello\nWorld')

    def test_empty_plain_falls_back_to_html_in_nested_alternative(self):
        msg = EmailMessage()
        msg.make_mixed()
        alternative = EmailMessage()
        alternative.make_alternative()
        alternative.attach(text_part(' \r\n '))
        alternative.attach(text_part('<p>HTML notice</p>', kind='html'))
        msg.attach(alternative)
        self.assertEqual(_body(msg), 'HTML notice')

    def test_plain_is_preferred_even_after_html(self):
        msg = EmailMessage()
        msg.make_alternative()
        msg.attach(text_part('<p>HTML notice</p>', kind='html'))
        msg.attach(text_part('Plain notice\n  Keep indentation'))
        self.assertEqual(_body(msg), 'Plain notice\n  Keep indentation')

    def test_attachment_only_remains_empty_and_attachment_bytes_unchanged(self):
        msg = EmailMessage()
        msg.make_mixed()
        content = '<html><script>secret()</script><form>Secure wrapper</form></html>'
        attachment = text_part(content, kind='html')
        attachment['Content-Disposition'] = 'attachment; filename="secure.html"'
        msg.attach(attachment)
        before = _attachments(msg)
        self.assertEqual(_body(msg), '')
        self.assertEqual(_attachments(msg), before)
        self.assertEqual(base64.b64decode(before[0]['content_b64']), content.encode())

    def test_inline_html_body_does_not_consume_secure_attachment(self):
        msg = EmailMessage()
        msg.make_mixed()
        msg.attach(text_part('<p>Delivery notice</p>', kind='html'))
        attachment = text_part('<script>secret()</script>', kind='html')
        attachment['Content-Disposition'] = 'attachment; filename="secure.html"'
        msg.attach(attachment)
        self.assertEqual(_body(msg), 'Delivery notice')
        self.assertEqual(base64.b64decode(_attachments(msg)[0]['content_b64']), b'<script>secret()</script>')

    def test_embedded_message_is_not_promoted_to_body(self):
        msg = EmailMessage()
        msg.make_mixed()
        forwarded = email.message.Message()
        forwarded.set_type('message/rfc822')
        forwarded.set_payload([text_part('Forwarded body')])
        msg.attach(forwarded)
        self.assertEqual(_body(msg), '')
    def test_single_part_binary_attachment_is_preserved_not_decoded_as_body(self):
        payload = b'PK\x03\x04\xff\x00Synthetic archive bytes'
        msg = email.message_from_bytes(
            b'Content-Type: application/zip\r\n'
            b'Content-Disposition: attachment; filename="sample.zip"\r\n'
            b'Content-Transfer-Encoding: base64\r\n\r\n' + base64.b64encode(payload))
        self.assertEqual(_body(msg), '')
        attachments = _attachments(msg)
        self.assertEqual(len(attachments), 1)
        self.assertEqual(base64.b64decode(attachments[0]['content_b64']), payload)
    def test_valid_plain_does_not_decode_unused_html_alternative(self):
        msg = EmailMessage()
        msg.make_alternative()
        msg.attach(text_part('Ignored', declared='not-a-real-charset', kind='html'))
        msg.attach(text_part('Valid plain'))
        self.assertEqual(_body(msg), 'Valid plain')

    def test_explicit_body_closes_optional_head_without_exposing_hidden_text(self):
        for markup in [
            '<html><head><title>Notice</title><body><p>Actual notice</p></body></html>',
            '<html><head><title>Notice</title><style>secret-style</style>'
            '<script>secret-script</script><body><p>Actual notice</p>'
            '<title>secret-title</title><style>secret-style</style>'
            '<script>secret-script</script></body></html>',
        ]:
            with self.subTest(markup=markup):
                self.assertEqual(_body(text_part(markup, kind='html')), 'Actual notice')

    def test_body_tag_inside_hidden_container_does_not_expose_its_text(self):
        for tag in ['script', 'style', 'title', 'template', 'iframe', 'object']:
            with self.subTest(tag=tag):
                markup = (f'<head><{tag}><body>secret</body></{tag}>'
                          '<body><p>Actual notice</p></body>')
                self.assertEqual(_body(text_part(markup, kind='html')), 'Actual notice')

    def test_html_table_cells_remain_separated(self):
        self.assertEqual(_body(text_part('<table><tr><td>Label</td><td>Value</td></tr></table>', kind='html')), 'Label Value')


if __name__ == "__main__":
    unittest.main()
