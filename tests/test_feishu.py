import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import app as a


class FeishuTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = a.DB_PATH
        a.DB_PATH = Path(self.tmp.name) / 'test.db'
        self.env = patch.dict(os.environ, {'TICKET_MAIL_WORKER':'0', 'TICKET_FEISHU_NOTIFY_WORKER':'0',
            'TICKET_FEISHU_APP_ID':'test-app', 'TICKET_FEISHU_APP_SECRET':'synthetic-secret',
            'TICKET_FEISHU_TENANT_KEY':'tenant', 'TICKET_PUBLIC_BASE_URL':'https://testserver'})
        self.env.start()
        a.init_db()
        with a.db() as c:
            ts = a.now()
            for uid in ['u1','u2']:
                c.execute('INSERT INTO users(id,username,display_name,password_hash,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                    (uid,uid,uid,a.password_hash('test-password'),ts,ts))
                c.execute("INSERT INTO workspace_memberships VALUES(?, 'geekforest','member',?)",(uid,ts))
        self.client = TestClient(a.app, base_url='https://testserver')
        self.token = a.session_token('u1','geekforest',int(time.time())+600)

    def tearDown(self):
        self.client.close()
        a.DB_PATH = self.old_db
        self.env.stop()
        self.tmp.cleanup()

    def login(self, uid='u1'):
        self.token = a.session_token(uid,'geekforest',int(time.time())+600)
        self.client.cookies.set('ticket_session',self.token)

    def headers(self):
        r = self.client.get('/api/auth/feishu/status')
        return {'Origin':'https://testserver','X-Feishu-CSRF':r.json()['csrf_token']}

    def begin_bind(self):
        r = self.client.post('/api/auth/feishu/bind-start', json={'password':'test-password','confirm':True}, headers=self.headers())
        self.assertEqual(200,r.status_code,r.text)
        from urllib.parse import urlsplit, parse_qs
        query = parse_qs(urlsplit(r.json()['authorization_url']).query)
        self.assertEqual(['S256'],query['code_challenge_method'])
        return query['state'][0]

    def callback(self, state, identity=None):
        import feishu
        with patch.object(feishu, 'exchange_identity', return_value=identity or {'open_id':'ou-one','tenant_key':'tenant','name':'Synthetic'}):
            return self.client.get('/api/auth/feishu/callback',params={'state':state,'code':'synthetic-code'}, follow_redirects=False)

    def bind(self):
        self.login()
        state = self.begin_bind()
        self.assertEqual(200,self.callback(state).status_code)
        r = self.client.post('/api/auth/feishu/finalize',json={'confirm':True},headers=self.headers())
        self.assertEqual(200,r.status_code,r.text)

    def test_status_pending_binding_verified_name_and_finalization(self):
        self.login()
        state = self.begin_bind()
        before = self.client.get('/api/auth/feishu/status?name=forged').json()
        self.assertIsNone(before.get('pending_binding'))
        self.assertEqual(200,self.callback(state).status_code)
        response = self.client.get('/api/auth/feishu/status?name=forged&open_id=forged')
        status = response.json()
        self.assertEqual({'name':'Synthetic'},status.get('pending_binding'))
        self.assertEqual('bind',status['pending_intent'])
        self.assertIsNone(status['binding'])
        for secret in (state, 'ou-one', 'synthetic-code', 'synthetic-secret', self.token):
            self.assertNotIn(secret,response.text)
        self.assertEqual(200,self.client.post('/api/auth/feishu/finalize',json={'confirm':True},headers=self.headers()).status_code)
        status = self.client.get('/api/auth/feishu/status').json()
        self.assertIsNone(status['pending_binding'])
        self.assertIsNone(status['pending_intent'])
        self.assertEqual('Synthetic',status['binding']['name'])

    def test_status_pending_binding_rejects_invalid_context(self):
        import feishu
        self.login()
        self.assertEqual(200,self.callback(self.begin_bind()).status_code)
        cookies = dict(self.client.cookies)
        with a.db() as c:
            original = dict(c.execute('SELECT * FROM feishu_oauth').fetchone())
        def hidden():
            response = self.client.get('/api/auth/feishu/status?name=Synthetic&feishu=confirm')
            self.assertEqual(200,response.status_code)
            self.assertIsNone(response.json()['pending_binding'])
            self.assertIsNone(response.json()['pending_intent'])
            self.assertNotIn('Synthetic',response.text)
        for key, value in [('expires',0), ('stage','started'), ('stage','consumed'),
                           ('app_id','other'), ('tenant_key','other'), ('redirect_uri','https://other/callback'),
                           ('intent','invalid'), ('intent','login'), ('user_id','u2'),
                           ('session_hash','other'), ('password_hash','other'), ('open_id','')]:
            with self.subTest(field=key,value=value):
                with a.db() as c:
                    c.execute('UPDATE feishu_oauth SET '+key+'=?',(value,))
                hidden()
                with a.db() as c:
                    c.execute('UPDATE feishu_oauth SET '+key+'=?',(original[key],))
        for cookie, value in [(feishu.COOKIE,None),(feishu.COOKIE,'wrong'),
                              ('ticket_session',None),('ticket_session','invalid'),
                              ('ticket_session',a.session_token('u2','geekforest',int(time.time())+600)),
                              ('ticket_session',a.session_token('u1','geekforest',int(time.time())+1200)),
                              ('ticket_session',a.session_token('u1','geekforest',int(time.time())-1))]:
            with self.subTest(cookie=cookie,value=value):
                self.client.cookies.clear()
                self.client.cookies.update({k:v for k,v in cookies.items() if k != cookie})
                if value is not None:
                    self.client.cookies.set(cookie,value)
                hidden()
        self.client.cookies.clear()
        self.client.cookies.update(cookies)
        for key in ['TICKET_FEISHU_APP_ID','TICKET_FEISHU_TENANT_KEY','TICKET_PUBLIC_BASE_URL','TICKET_FEISHU_APP_SECRET']:
            with self.subTest(config=key), patch.dict(os.environ,{key:''}):
                hidden()
        with a.db() as c:
            c.execute("UPDATE users SET enabled=0 WHERE id='u1'")
        hidden()

    def test_status_pending_login_has_no_binding_name(self):
        from urllib.parse import urlsplit, parse_qs
        response = self.client.get('/api/auth/feishu/start',follow_redirects=False)
        state = parse_qs(urlsplit(response.headers['location']).query)['state'][0]
        self.assertEqual(200,self.callback(state).status_code)
        response = self.client.get('/api/auth/feishu/status')
        self.assertEqual('login',response.json()['pending_intent'])
        self.assertIsNone(response.json()['pending_binding'])
        self.assertNotIn('Synthetic',response.text)

    def test_malformed_config_is_disabled(self):
        import feishu
        with patch.dict(os.environ,{'TICKET_PUBLIC_BASE_URL':'https://[invalid'}):
            self.assertIsNone(feishu.config())
            response = self.client.get('/api/auth/feishu/status')
            self.assertFalse(response.json()['enabled'])
            self.assertIsNone(response.json()['pending_binding'])
            self.assertEqual(503,self.client.get('/api/auth/feishu/start').status_code)

    def test_feishu_routes_and_errors_are_no_store(self):
        def check(response, code):
            self.assertEqual(code,response.status_code,response.text)
            self.assertEqual('no-store',response.headers.get('cache-control'))
        check(self.client.get('/api/notifications/feishu/subscriptions'),401)
        check(self.client.get('/api/auth/feishu/callback'),400)
        check(self.client.get('/api/auth/feishu/start',follow_redirects=False),303)
        self.login()
        check(self.client.post('/api/auth/feishu/bind-start',json={}),422)
        check(self.client.post('/api/auth/feishu/bind-start',json={'password':'test-password','confirm':True}),403)
        self.bind()
        check(self.client.get('/api/notifications/feishu/subscriptions'),200)
        check(self.subscribe(),200)
        ticket = self.incoming('cache-test')['ticket_id']
        check(self.client.post('/api/notifications/feishu/open-ticket',json={'ticket_id':ticket},headers=self.headers()),200)
        check(self.client.post('/api/auth/feishu/unlink',json={'password':'test-password','confirm':True},headers=self.headers()),200)
        with patch.dict(os.environ,{'TICKET_FEISHU_APP_SECRET':''}):
            check(self.client.get('/api/auth/feishu/start'),503)

    def test_binding_requires_password_origin_confirmation_and_preserves_permissions(self):
        self.login()
        self.assertEqual(403,self.client.post('/api/auth/feishu/bind-start',json={'password':'test-password','confirm':True}).status_code)
        self.assertEqual(401,self.client.post('/api/auth/feishu/bind-start',json={'password':'wrong','confirm':True},headers=self.headers()).status_code)
        self.assertEqual(400,self.client.post('/api/auth/feishu/bind-start',json={'password':'test-password','confirm':False},headers=self.headers()).status_code)
        self.bind()
        status = self.client.get('/api/auth/feishu/status').json()
        self.assertTrue(status['bound'])
        self.assertNotIn('open_id',str(status))
        with a.db() as c:
            self.assertEqual(0,c.execute("SELECT is_admin FROM users WHERE id='u1'").fetchone()[0])
            self.assertEqual('member',c.execute("SELECT role FROM workspace_memberships WHERE user_id='u1'").fetchone()[0])
            self.assertEqual(2,c.execute('SELECT count(*) FROM users').fetchone()[0])

    def sso(self):
        self.client.cookies.clear()
        from urllib.parse import urlsplit, parse_qs
        r = self.client.get('/api/auth/feishu/start',follow_redirects=False)
        self.assertEqual(303,r.status_code)
        state = parse_qs(urlsplit(r.headers['location']).query)['state'][0]
        self.assertEqual(200,self.callback(state).status_code)
        return self.client.post('/api/auth/feishu/finalize',json={'confirm':True},headers=self.headers())

    def test_sso_only_bound_existing_user_unlink_revokes_sso_not_password_sessions(self):
        self.assertEqual(403,self.sso().status_code)
        self.bind()
        password_token = self.token
        r = self.sso()
        self.assertEqual(200,r.status_code,r.text)
        sso_token = self.client.cookies.get('ticket_session')
        self.assertIsNotNone(a.session_context(sso_token))
        switched = self.client.post('/api/workspaces/switch',json={'workspace_id':'geekforest'})
        self.assertEqual(200,switched.status_code)
        switched_token = self.client.cookies.get('ticket_session')
        r = self.client.post('/api/auth/feishu/unlink',json={'password':'test-password','confirm':True},headers=self.headers())
        self.assertEqual(200,r.status_code,r.text)
        self.assertIsNone(a.session_context(sso_token))
        self.assertIsNone(a.session_context(switched_token))
        self.assertIsNotNone(a.session_context(password_token))

    def test_state_replay_expiry_cookie_tenant_and_original_account(self):
        self.login()
        state = self.begin_bind()
        saved = dict(self.client.cookies)
        self.client.cookies.clear()
        self.assertEqual(400,self.callback(state).status_code)
        self.client.cookies.update(saved)
        self.assertEqual(403,self.callback(state,{'open_id':'ou-one','tenant_key':'evil'}).status_code)
        self.assertEqual(400,self.callback(state).status_code)
        state = self.begin_bind()
        with a.db() as c:
            c.execute('UPDATE feishu_oauth SET expires=0')
        self.assertEqual(400,self.callback(state).status_code)
        state = self.begin_bind()
        self.assertEqual(200,self.callback(state).status_code)
        self.assertEqual(400,self.callback(state).status_code)
        self.login('u2')
        self.assertEqual(403,self.client.post('/api/auth/feishu/finalize',json={'confirm':True},headers=self.headers()).status_code)
        with a.db() as c:
            self.assertEqual(0,c.execute('SELECT count(*) FROM feishu_bindings').fetchone()[0])

    def test_identity_uniqueness_no_silent_rebinding(self):
        self.bind()
        self.login('u2')
        state = self.begin_bind()
        self.callback(state)
        self.assertEqual(409,self.client.post('/api/auth/feishu/finalize',json={'confirm':True},headers=self.headers()).status_code)
        self.login()
        self.assertEqual(409,self.client.post('/api/auth/feishu/bind-start',json={'password':'test-password','confirm':True},headers=self.headers()).status_code)

    def subscribe(self, enabled=True, box='support'):
        return self.client.put('/api/notifications/feishu/subscriptions/'+box,json={'enabled':enabled},headers=self.headers())

    def incoming(self, provider='new-1', **kw):
        values = dict(mailbox_id='support', sender_name='Synthetic', sender_email='sender@example.com', subject='Private subject',body='Private body',provider_message_id=provider)
        values.update(kw)
        with patch.object(a,'mailbox_can_auto_create_ticket',return_value=True):
            return a.receive_mail(a.IncomingMail(**values))

    def jobs(self):
        with a.db() as c:
            return [dict(r) for r in c.execute('SELECT * FROM feishu_notification_outbox ORDER BY rowid')]

    def test_opt_in_new_reply_history_duplicate_and_subscription_generation(self):
        self.bind()
        listed = self.client.get('/api/notifications/feishu/subscriptions')
        self.assertEqual(200,listed.status_code,listed.text)
        self.assertTrue(all(not x['enabled'] for x in listed.json()['items']))
        self.incoming('unsubscribed')
        self.assertEqual([],self.jobs())
        self.assertEqual(200,self.subscribe().status_code)
        first = self.incoming()
        self.incoming('reply',in_reply_to_ticket=first['ticket_id'])
        self.incoming('history',historical=True)
        self.incoming('new-1')
        self.assertEqual(2,len(self.jobs()))
        self.assertEqual(200,self.subscribe(False).status_code)
        self.assertTrue(all(j['status']=='cancelled' for j in self.jobs()))
        self.subscribe()
        self.incoming('later')
        self.assertNotEqual(self.jobs()[0]['subscription_generation'],self.jobs()[-1]['subscription_generation'])
        self.client.post('/api/auth/feishu/unlink',json={'password':'test-password','confirm':True},headers=self.headers())
        self.assertTrue(all(j['status']=='cancelled' for j in self.jobs()))
        self.bind()
        self.assertTrue(all(not x['enabled'] for x in self.client.get('/api/notifications/feishu/subscriptions').json()['items']))

    def test_subscription_http_inbound_and_parent_isolation(self):
        self.bind()
        with a.db() as c:
            c.execute("INSERT INTO mailboxes(id,name,email,color,created_at,workspace_id) VALUES('foreign','foreign','foreign@example.com','#000',?,'eddy-personal')",(a.now(),))
        self.assertEqual(403,self.subscribe(box='foreign').status_code)
        self.assertEqual(403,self.client.post('/api/mail/incoming',json={'mailbox_id':'foreign','sender_name':'x','sender_email':'x@example.com','subject':'x','body':'x'},headers=self.headers()).status_code)
        other = self.incoming('other',mailbox_id='foreign')
        self.subscribe()
        here = self.incoming('here',in_reply_to_ticket=other['ticket_id'])
        self.assertNotEqual(other['ticket_id'],here['ticket_id'])
        self.assertEqual(here['ticket_id'],self.jobs()[0]['ticket_id'])
        self.assertEqual(403,self.client.post('/api/notifications/feishu/open-ticket',json={'ticket_id':other['ticket_id']},headers=self.headers()).status_code)
        with a.db() as c:
            c.execute("INSERT INTO workspace_memberships VALUES('u1','eddy-personal','member',?)",(a.now(),))
        r = self.client.post('/api/notifications/feishu/open-ticket',json={'ticket_id':other['ticket_id']},headers=self.headers())
        self.assertEqual(200,r.status_code,r.text)
        self.assertEqual('eddy-personal',self.client.get('/api/session').json()['user']['workspace_id'])

    def test_worker_private_send_retries_lease_recovery_and_dedupe_deadline(self):
        import feishu
        self.bind(); self.subscribe(); self.incoming()
        worker = feishu.NotificationWorker(a)
        sent = []
        def fake(url,data=None,token=None,form=False):
            if 'tenant_access_token' in url:
                return {'code':0,'tenant_access_token':'fake-token'}
            sent.append(data)
            if len(sent)==1:
                raise TimeoutError('synthetic ambiguous delivery')
            return {'code':0,'data':{'message_id':'vendor-one'}}
        with patch.object(feishu,'transport',side_effect=fake):
            self.assertEqual(1,worker.run_once())
            self.assertEqual('queued',self.jobs()[0]['status'])
            with a.db() as c:
                c.execute("UPDATE feishu_notification_outbox SET status='sending',lease_until=0,next_attempt=0")
            self.assertEqual(1,worker.run_once())
        self.assertEqual('sent',self.jobs()[0]['status'])
        self.assertEqual('vendor-one',self.jobs()[0]['vendor_message_id'])
        self.assertEqual(sent[0]['uuid'],sent[1]['uuid'])
        self.assertLessEqual(len(sent[0]['uuid']),50)
        self.assertEqual('ou-one',sent[0]['receive_id'])
        import json
        text = json.loads(sent[0]['content'])['text']
        self.assertIn('https://testserver/?ticket=',text)
        self.assertNotIn('Private',text)
        self.incoming('expired')
        with a.db() as c:
            c.execute("UPDATE feishu_notification_outbox SET status='sending',lease_until=0,first_attempt=? WHERE status='queued'",(time.time()-3601,))
        with patch.object(feishu,'transport',side_effect=AssertionError('must not retry')):
            worker.run_once()
        self.assertEqual('uncertain',self.jobs()[-1]['status'])

    def test_worker_rechecks_membership_and_is_disabled_without_config(self):
        import feishu
        self.bind(); self.subscribe(); self.incoming()
        worker = feishu.NotificationWorker(a)
        self.assertIsNone(worker.thread)
        worker.start()
        self.assertIsNone(worker.thread)
        with patch.dict(os.environ,{'TICKET_FEISHU_APP_SECRET':''}), patch.object(feishu,'transport',side_effect=AssertionError('disabled')):
            self.assertEqual(0,worker.run_once())
        with a.db() as c:
            c.execute("DELETE FROM workspace_memberships WHERE user_id='u1'")
        with patch.object(feishu,'transport',side_effect=AssertionError('revoked')):
            worker.run_once()
        self.assertEqual('cancelled',self.jobs()[0]['status'])

    def test_migration_twice_preserves_accounts_permissions_and_tickets(self):
        self.incoming('before-migration')
        tables = ['users','workspace_memberships','tickets','messages']
        with a.db() as c:
            before = {t:[tuple(r) for r in c.execute('SELECT * FROM '+t)] for t in tables}
            # Simulate a copied pre-feature fixture: existing business data, no
            # Feishu tables. Both migration passes must preserve every row.
            for table in ['feishu_notification_outbox','feishu_subscriptions','feishu_oauth','feishu_bindings','feishu_audit']:
                c.execute('DROP TABLE '+table)
        a.init_db(); a.init_db()
        with a.db() as c:
            self.assertEqual(before,{t:[tuple(r) for r in c.execute('SELECT * FROM '+t)] for t in tables})
            for table in ['feishu_bindings','feishu_subscriptions','feishu_notification_outbox']:
                self.assertEqual(0,c.execute('SELECT count(*) FROM '+table).fetchone()[0])

    def test_oauth_v3_form_pkce_and_envelope_fake_http(self):
        import feishu, io, json
        from urllib.parse import parse_qs
        calls = []
        def fake(req,timeout):
            calls.append(req)
            if len(calls)==1:
                return io.BytesIO(json.dumps({'code':0,'access_token':'synthetic-user-token'}).encode())
            return io.BytesIO(json.dumps({'code':0,'data':{'open_id':'ou','tenant_key':'tenant'}}).encode())
        with patch('urllib.request.urlopen',side_effect=fake):
            identity = feishu.exchange_identity(feishu.config(),{'redirect_uri':'https://testserver/api/auth/feishu/callback','verifier':'v'*43},'code')
        self.assertEqual('ou',identity['open_id'])
        self.assertEqual('https://accounts.feishu.cn/oauth/v3/token',calls[0].full_url)
        self.assertEqual(['v'*43],parse_qs(calls[0].data.decode())['code_verifier'])
        self.assertEqual('Bearer synthetic-user-token',calls[1].get_header('Authorization'))
        with patch('urllib.request.urlopen',return_value=io.BytesIO(b'{"code":123,"data":{"open_id":"ou"}}')):
            with self.assertRaises(ValueError):
                feishu.transport('https://open.feishu.cn/test')

    def test_disabled_user_membership_and_mutation_csrf(self):
        self.bind()
        r = self.client.put('/api/notifications/feishu/subscriptions/support',json={'enabled':True},headers={'Origin':'https://testserver'})
        self.assertEqual(403,r.status_code)
        r = self.client.put('/api/notifications/feishu/subscriptions/support',json={'enabled':True},headers={**self.headers(),'Origin':'https://testserver.evil'})
        self.assertEqual(403,r.status_code)
        with a.db() as c:
            c.execute("UPDATE users SET enabled=0 WHERE id='u1'")
        self.assertEqual(403,self.sso().status_code)
        self.assertIsNone(a.session_context(self.token))
        with a.db() as c:
            c.execute("UPDATE users SET enabled=1 WHERE id='u1'")
            c.execute("DELETE FROM workspace_memberships WHERE user_id='u1'")
        self.assertEqual(403,self.sso().status_code)

    def test_inflight_token_fetch_revocation_prevents_send(self):
        import feishu
        self.bind(); self.subscribe(); self.incoming()
        def revoke(url,data=None,token=None,form=False):
            self.assertIn('tenant_access_token',url)
            with a.db() as c:
                c.execute("UPDATE mailboxes SET enabled=0 WHERE id='support'")
            return {'code':0,'tenant_access_token':'synthetic'}
        with patch.object(feishu,'transport',side_effect=revoke):
            feishu.NotificationWorker(a).run_once()
        self.assertEqual('cancelled',self.jobs()[0]['status'])

    def test_jobs_transactional_skip_outbound_and_no_network_ingestion(self):
        import feishu
        self.bind(); self.subscribe()
        with patch('urllib.request.urlopen',side_effect=AssertionError('ingestion network')):
            self.incoming('transactional')
        with a.db() as c:
            before = c.execute('SELECT count(*) FROM messages').fetchone()[0]
        with patch.object(feishu,'enqueue',side_effect=RuntimeError('rollback')):
            with self.assertRaises(RuntimeError):
                self.incoming('rolled-back')
        with a.db() as c:
            self.assertEqual(before,c.execute('SELECT count(*) FROM messages').fetchone()[0])
        with patch.object(a,'mailbox_can_auto_create_ticket',return_value=False):
            result = a.receive_mail(a.IncomingMail(mailbox_id='support',sender_name='x',sender_email='x@example.com',subject='skip',body='skip'))
            self.assertTrue(result['skipped'])
        self.assertEqual(1,len(self.jobs()))
        ticket_id = self.jobs()[0]['ticket_id']
        r = self.client.post('/api/tickets/'+ticket_id+'/reply',json={'body':'synthetic reply'})
        self.assertEqual(200,r.status_code,r.text)
        self.assertEqual(1,len(self.jobs()))

    def test_callback_is_same_origin_bridge_and_bind_needs_original_strict_cookie(self):
        self.login()
        state = self.begin_bind()
        password_cookie = self.token
        self.client.cookies.delete('ticket_session')
        r = self.callback(state)
        self.assertEqual(200,r.status_code)
        self.assertIn('text/html',r.headers['content-type'])
        self.assertIn('/?feishu=confirm',r.text)
        self.assertNotIn(state,r.text)
        self.assertEqual(403,self.client.post('/api/auth/feishu/finalize',json={'confirm':True},headers=self.headers()).status_code)
        self.client.cookies.set('ticket_session',password_cookie)
        self.assertEqual(200,self.client.post('/api/auth/feishu/finalize',json={'confirm':True},headers=self.headers()).status_code)

    def test_worker_canonical_origin_change_cancels_stable_uuid_payload(self):
        import feishu
        self.bind(); self.subscribe(); self.incoming()
        with patch.dict(os.environ,{'TICKET_PUBLIC_BASE_URL':'https://new.example.com'}), patch.object(feishu,'transport',side_effect=AssertionError('changed payload')):
            feishu.NotificationWorker(a).run_once()
        self.assertEqual('cancelled',self.jobs()[0]['status'])

    def test_http_errors_retry_with_backoff_without_sensitive_error_storage(self):
        import feishu
        from urllib.error import HTTPError
        self.bind(); self.subscribe(); self.incoming()
        worker = feishu.NotificationWorker(a)
        with patch('urllib.request.urlopen',side_effect=HTTPError('https://open.feishu.cn',500,'SENSITIVE',{},None)):
            worker.run_once()
        job = self.jobs()[0]
        self.assertEqual('queued',job['status'])
        self.assertEqual(1,job['attempts'])
        self.assertGreater(job['next_attempt'],time.time())
        self.assertEqual('FEISHU_SEND_FAILED',job['last_error'])
        with patch('urllib.request.urlopen',side_effect=AssertionError('backoff')):
            self.assertEqual(0,worker.run_once())

    def test_http_duplicate_cannot_disclose_foreign_ticket(self):
        self.login()
        with a.db() as c:
            c.execute("INSERT INTO mailboxes(id,name,email,color,created_at,workspace_id) VALUES('foreign','foreign','foreign@example.com','#000',?,'eddy-personal')",(a.now(),))
        foreign = self.incoming('foreign-id',mailbox_id='foreign')
        r = self.client.post('/api/mail/incoming',headers=self.headers(),json={'mailbox_id':'support','sender_name':'x','sender_email':'x@example.com','subject':'x','body':'x','provider_message_id':'foreign-id'})
        self.assertEqual(409,r.status_code)
        self.assertNotIn(foreign['ticket_id'],r.text)

    def test_logged_out_ticket_link_preserved_without_arbitrary_redirect(self):
        r = self.client.get('/?ticket=TKT-1050',follow_redirects=False)
        self.assertEqual('/login?ticket=TKT-1050',r.headers['location'])
        r = self.client.get('/?ticket=https://evil.example',follow_redirects=False)
        self.assertEqual('/login',r.headers['location'])
        self.login()
        r = self.client.get('/login?ticket=TKT-1050',follow_redirects=False)
        self.assertEqual('/?ticket=TKT-1050',r.headers['location'])

    def test_disabled_status_and_start_never_network(self):
        with patch.dict(os.environ, {'TICKET_FEISHU_APP_SECRET':''}), patch('urllib.request.urlopen', side_effect=AssertionError('network')):
            r = self.client.get('/api/auth/feishu/status')
            self.assertEqual(200,r.status_code)
            self.assertFalse(r.json()['enabled'])
            self.assertEqual(503,self.client.get('/api/auth/feishu/start').status_code)
