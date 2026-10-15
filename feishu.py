"""Opt-in Feishu identity and private notification boundary. No startup network IO."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
import uuid

from typing import NoReturn
import logging

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

COOKIE = 'ticket_feishu_oauth'
TTL = 600


def config():
    values = [os.getenv(k, '').strip() for k in ('TICKET_FEISHU_APP_ID', 'TICKET_FEISHU_APP_SECRET',
              'TICKET_FEISHU_TENANT_KEY', 'TICKET_PUBLIC_BASE_URL')]
    app_id, secret, tenant, base = values
    try:
        parsed = urllib.parse.urlsplit(base)
    except ValueError:
        return None
    if not all(values) or parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        return None
    return dict(app_id=app_id, secret=secret, tenant=tenant, base=base,
                callback=base + '/api/auth/feishu/callback')


def fail(code, status=400) -> NoReturn:
    raise HTTPException(status, detail={'error': code})


def required_config():
    cfg = config()
    if not cfg:
        fail('FEISHU_DISABLED', 503)
    return cfg


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def transport(url, data=None, token=None, form=False):
    headers = {'Content-Type': 'application/x-www-form-urlencoded' if form else 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    body = (urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()) if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        result = json.loads(response.read(1024 * 1024))
    if not isinstance(result, dict) or result.get('code') != 0:
        raise ValueError('FEISHU_API_FAILED')
    return result


def exchange_identity(cfg, row, code):
    token = transport('https://accounts.feishu.cn/oauth/v3/token', {
        'grant_type':'authorization_code', 'client_id':cfg['app_id'], 'client_secret':cfg['secret'],
        'code':code, 'redirect_uri':row['redirect_uri'], 'code_verifier':row['verifier']}, form=True)
    access = token.get('access_token')
    if not isinstance(access, str) or not access:
        raise ValueError('FEISHU_TOKEN_INVALID')
    return transport('https://open.feishu.cn/open-apis/authen/v1/user_info', token=access).get('data', {})


def csrf(a, request):
    source = request.cookies.get('ticket_session') or request.cookies.get(COOKIE) or ''
    return hmac.new(a.SESSION_SECRET.encode(), ('feishu-csrf:' + source).encode(), hashlib.sha256).hexdigest() if source else ''


def mutation(a, request):
    cfg = required_config()
    if request.headers.get('origin') != cfg['base']:
        fail('ORIGIN_REJECTED', 403)
    expected = csrf(a, request)
    if not expected or not hmac.compare_digest(expected, request.headers.get('x-feishu-csrf','')):
        fail('CSRF_REJECTED', 403)
    return cfg


class Confirm(BaseModel):
    confirm: bool = False


class Reauth(Confirm):
    password: str = Field(min_length=1, max_length=500)


def reauth(a, request, payload, ctx):
    if payload.confirm is not True:
        fail('CONFIRMATION_REQUIRED')
    key = 'feishu:' + ctx['id']
    recent = [t for t in a.login_attempts.get(key, []) if time.time()-t < 900]
    if len(recent) >= 10:
        fail('TOO_MANY_ATTEMPTS', 429)
    with a.db() as c:
        user = c.execute('SELECT password_hash FROM users WHERE id=? AND enabled=1',(ctx['id'],)).fetchone()
    if not user or not a.password_matches(payload.password,user['password_hash']):
        a.login_attempts[key] = recent + [time.time()]
        fail('INVALID_CREDENTIALS', 401)
    a.login_attempts.pop(key, None)
    return user['password_hash']


def audit(c, uid, action):
    c.execute('INSERT INTO feishu_audit VALUES(?,?,?,?)',(str(uuid.uuid4()),uid,action,time.time()))


def new_flow(a, request, intent, cfg, ctx=None, password=None):
    state, correlation, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    with a.db() as c:
        c.execute('DELETE FROM feishu_oauth WHERE expires<? OR correlation_hash=?',(time.time(),digest(request.cookies.get(COOKIE,''))))
        c.execute('INSERT INTO feishu_oauth(state_hash,correlation_hash,intent,user_id,session_hash,password_hash,app_id,tenant_key,redirect_uri,verifier,expires,stage) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (digest(state),digest(correlation),intent,ctx['id'] if ctx else None,
             digest(request.cookies.get('ticket_session','')) if ctx else None,password,
             cfg['app_id'],cfg['tenant'],cfg['callback'],verifier,time.time()+TTL,'started'))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    url = 'https://accounts.feishu.cn/open-apis/authen/v1/authorize?' + urllib.parse.urlencode({
        'client_id':cfg['app_id'],'response_type':'code','redirect_uri':cfg['callback'],'state':state,
        'code_challenge':challenge,'code_challenge_method':'S256'})
    response = RedirectResponse(url,303) if intent == 'login' else JSONResponse({'ok':True,'authorization_url':url})
    response.set_cookie(COOKIE,correlation,max_age=TTL,httponly=True,secure=True,samesite='lax',path='/')
    response.headers['Cache-Control'] = 'no-store'
    return response


class Subscription(BaseModel):
    enabled: bool


class OpenTicket(BaseModel):
    ticket_id: str = Field(min_length=1, max_length=100)


def cancel_jobs(c, uid, mailbox=None):
    sql = "UPDATE feishu_notification_outbox SET status='cancelled',last_error='REVOKED' WHERE user_id=? AND status IN ('queued','sending')"
    c.execute(sql + (' AND mailbox_id=?' if mailbox else ''),(uid,mailbox) if mailbox else (uid,))


def enqueue(c, message_id, ticket_id, mailbox):
    cfg = config()
    if not cfg:
        return
    rows = c.execute('''SELECT b.*,s.generation subscription_generation FROM feishu_subscriptions s
        JOIN feishu_bindings b ON b.user_id=s.user_id JOIN users u ON u.id=s.user_id AND u.enabled=1
        JOIN mailboxes mb ON mb.id=s.mailbox_id AND mb.enabled=1
        JOIN workspace_memberships m ON m.user_id=s.user_id AND m.workspace_id=mb.workspace_id
        WHERE s.enabled=1 AND s.mailbox_id=? AND b.app_id=? AND b.tenant_key=?''',
        (mailbox['id'],cfg['app_id'],cfg['tenant'])).fetchall()
    for row in rows:
        c.execute('''INSERT OR IGNORE INTO feishu_notification_outbox
            (id,message_id,ticket_id,mailbox_id,workspace_id,user_id,app_id,tenant_key,open_id,canonical_base,binding_generation,subscription_generation,status,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'queued',?)''',
            (str(uuid.uuid4()),message_id,ticket_id,mailbox['id'],mailbox['workspace_id'],row['user_id'],
             row['app_id'],row['tenant_key'],row['open_id'],cfg['base'],row['generation'],row['subscription_generation'],time.time()))


class NotificationWorker:
    """One bounded sender; durable claims support multiple processes and restart.

    Vendor UUID deduplication lasts one hour, not forever. All ambiguous outcomes
    stop after 55 minutes or 8 claims, including crashes. Never auto-resubmit an
    uncertain row. Revocation cannot retract a request already in flight.
    """
    def __init__(self, a):
        self.a = a
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        if not config() or os.getenv('TICKET_FEISHU_NOTIFY_WORKER','0') != '1' or self.thread:
            return
        self.thread = threading.Thread(target=self.run, name='feishu-notifications', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=25)

    def run(self):
        while not self.stop_event.is_set():
            try:
                # One job/cycle keeps network and database load bounded.
                self.run_once()
            except Exception as exc:
                logging.getLogger('ticket-feishu').warning('worker failure type=%s',type(exc).__name__)
            self.stop_event.wait(2)

    def authorized(self, c, job, cfg):
        if job['app_id'] != cfg['app_id'] or job['tenant_key'] != cfg['tenant'] or job['canonical_base'] != cfg['base']:
            return False
        return bool(c.execute('''SELECT 1 FROM feishu_bindings b
            JOIN users u ON u.id=b.user_id AND u.enabled=1
            JOIN feishu_subscriptions s ON s.user_id=u.id AND s.mailbox_id=? AND s.enabled=1
            JOIN mailboxes mb ON mb.id=s.mailbox_id AND mb.enabled=1 AND mb.workspace_id=?
            JOIN workspace_memberships m ON m.user_id=u.id AND m.workspace_id=mb.workspace_id
            JOIN tickets t ON t.id=? AND t.mailbox_id=mb.id
            JOIN messages msg ON msg.id=? AND msg.ticket_id=t.id AND msg.direction='inbound'
            WHERE b.user_id=? AND b.app_id=? AND b.tenant_key=? AND b.open_id=?
            AND b.generation=? AND s.generation=?''',
            (job['mailbox_id'],job['workspace_id'],job['ticket_id'],job['message_id'],job['user_id'],
             job['app_id'],job['tenant_key'],job['open_id'],job['binding_generation'],job['subscription_generation'])).fetchone())

    def run_once(self):
        cfg = config()
        if not cfg:
            return 0
        current = time.time()
        lease = secrets.token_urlsafe(24)
        with self.a.db() as c:
            c.execute('BEGIN IMMEDIATE')
            job = c.execute('''SELECT * FROM feishu_notification_outbox
                WHERE (status='queued' AND next_attempt<=?) OR (status='sending' AND lease_until<=?)
                ORDER BY created_at LIMIT 1''',(current,current)).fetchone()
            if not job:
                return 0
            if not self.authorized(c,job,cfg):
                c.execute("UPDATE feishu_notification_outbox SET status='cancelled',last_error='REVOKED' WHERE id=?",(job['id'],))
                return 1
            if job['attempts'] >= 8 or (job['first_attempt'] is not None and current-job['first_attempt'] >= 3300):
                c.execute("UPDATE feishu_notification_outbox SET status='uncertain',last_error='RETRY_WINDOW_EXHAUSTED' WHERE id=?",(job['id'],))
                return 1
            c.execute("""UPDATE feishu_notification_outbox SET status='sending',lease_until=?,lease_token=?,
                attempts=attempts+1,first_attempt=COALESCE(first_attempt,?) WHERE id=?""",(current+60,lease,current,job['id']))
        outcome, message_id, error = 'queued', None, None
        try:
            token_result = transport('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
                {'app_id':cfg['app_id'],'app_secret':cfg['secret']})
            token = token_result.get('tenant_access_token')
            if not isinstance(token,str) or not token:
                raise ValueError('INVALID_TENANT_TOKEN')
            # Recheck AFTER fetching a token, immediately before sending. No HTTP
            # happens in the mail ingestion transaction or while holding its lock.
            with self.a.db() as c:
                claimed = c.execute("SELECT * FROM feishu_notification_outbox WHERE id=? AND status='sending' AND lease_token=?",
                    (job['id'],lease)).fetchone()
                allowed = bool(claimed and claimed['lease_until'] > time.time() and self.authorized(c,claimed,cfg))
            if not allowed:
                outcome, error = 'cancelled', 'REVOKED'
            elif time.time() - claimed['first_attempt'] >= 3300:
                outcome, error = 'uncertain', 'RETRY_WINDOW_EXHAUSTED'
            else:
                text = 'New inbound message · ' + job['ticket_id'] + '\n' + cfg['base'] + '/?ticket=' + urllib.parse.quote(job['ticket_id'],safe='')
                result = transport('https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id',
                    {'receive_id':job['open_id'],'msg_type':'text','content':json.dumps({'text':text},ensure_ascii=False),'uuid':job['id']},token=token)
                message_id = result.get('data',{}).get('message_id')
                if not isinstance(message_id,str) or not message_id:
                    raise ValueError('MISSING_MESSAGE_ID')
                outcome = 'sent'
        except Exception:
            # Provider responses and exceptions can contain tokens or private data.
            error = 'FEISHU_SEND_FAILED'
            if job['attempts'] + 1 >= 8 or time.time()-(job['first_attempt'] or current) >= 3300:
                outcome = 'uncertain'
        with self.a.db() as c:
            c.execute('''UPDATE feishu_notification_outbox SET status=?,vendor_message_id=?,last_error=?,next_attempt=?,lease_until=0
                WHERE id=? AND status='sending' AND lease_token=?''',
                (outcome,message_id,error,time.time()+min(300,10*(2**job['attempts'])),job['id'],lease))
        return 1


def install(a):
    @a.app.middleware('http')
    async def feishu_no_store(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(('/api/auth/feishu/', '/api/notifications/feishu/')):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @a.app.get('/api/notifications/feishu/subscriptions')
    def subscriptions(request: Request):
        ctx = a.current_context(request)
        with a.db() as c:
            items = [dict(r) for r in c.execute('''SELECT mb.id mailbox_id,mb.name,mb.workspace_id,COALESCE(s.enabled,0) enabled
                FROM mailboxes mb LEFT JOIN feishu_subscriptions s ON s.mailbox_id=mb.id AND s.user_id=?
                WHERE mb.workspace_id=? AND mb.enabled=1 ORDER BY mb.name''',(ctx['id'],ctx['workspace_id']))]
        return JSONResponse({'ok':True,'items':items},headers={'Cache-Control':'no-store'})

    @a.app.put('/api/notifications/feishu/subscriptions/{mailbox_id}')
    def subscription(mailbox_id: str, payload: Subscription, request: Request):
        cfg = mutation(a,request)
        ctx = a.current_context(request)
        with a.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT 1 FROM mailboxes WHERE id=? AND workspace_id=? AND enabled=1',(mailbox_id,ctx['workspace_id'])).fetchone():
                fail('MAILBOX_FORBIDDEN',403)
            if not c.execute('SELECT 1 FROM feishu_bindings WHERE user_id=? AND app_id=? AND tenant_key=?',(ctx['id'],cfg['app_id'],cfg['tenant'])).fetchone():
                fail('EXISTING_BINDING_REQUIRED',403)
            old = c.execute('SELECT enabled FROM feishu_subscriptions WHERE user_id=? AND mailbox_id=?',(ctx['id'],mailbox_id)).fetchone()
            if not old or bool(old['enabled']) != payload.enabled:
                c.execute('''INSERT INTO feishu_subscriptions VALUES(?,?,?,?,?) ON CONFLICT(user_id,mailbox_id)
                    DO UPDATE SET enabled=excluded.enabled,generation=excluded.generation,updated_at=excluded.updated_at''',
                    (ctx['id'],mailbox_id,int(payload.enabled),str(uuid.uuid4()),time.time()))
                cancel_jobs(c,ctx['id'],mailbox_id)
                audit(c,ctx['id'],'subscribe' if payload.enabled else 'unsubscribe')
        return {'ok':True,'mailbox_id':mailbox_id,'enabled':payload.enabled}

    @a.app.post('/api/notifications/feishu/open-ticket')
    def open_ticket(payload: OpenTicket, request: Request):
        mutation(a,request)
        ctx = a.current_context(request)
        with a.db() as c:
            row = c.execute('''SELECT mb.workspace_id FROM tickets t JOIN mailboxes mb ON mb.id=t.mailbox_id
                JOIN workspace_memberships m ON m.workspace_id=mb.workspace_id AND m.user_id=?
                WHERE t.id=? AND mb.enabled=1''',(ctx['id'],payload.ticket_id)).fetchone()
        if not row:
            fail('TICKET_FORBIDDEN',403)
        response = JSONResponse({'ok':True,'ticket_id':payload.ticket_id,'workspace_id':row['workspace_id']})
        response.set_cookie('ticket_session',a.session_token(ctx['id'],row['workspace_id'],int(time.time())+a.SESSION_TTL,ctx.get('feishu_generation')),
            max_age=a.SESSION_TTL,httponly=True,secure=True,samesite='strict',path='/')
        return response

    @a.app.get('/api/auth/feishu/status')
    def status(request: Request):
        ctx = a.session_context(request.cookies.get('ticket_session'))
        cfg = config()
        with a.db() as c:
            binding = c.execute('SELECT name,bound_at FROM feishu_bindings WHERE user_id=? AND app_id=? AND tenant_key=?',
                (ctx['id'],cfg['app_id'],cfg['tenant'])).fetchone() if ctx and cfg else None
            pending = c.execute("""SELECT * FROM feishu_oauth WHERE correlation_hash=?
                AND stage='verified' AND expires>? AND app_id=? AND tenant_key=? AND redirect_uri=?
                AND intent IN ('login','bind') AND open_id IS NOT NULL AND length(open_id) BETWEEN 1 AND 200""",
                (digest(request.cookies[COOKIE]),time.time(),cfg['app_id'],cfg['tenant'],cfg['callback'])).fetchone() if cfg and request.cookies.get(COOKIE) else None
            if pending and pending['intent'] == 'bind':
                user = c.execute('SELECT password_hash FROM users WHERE id=? AND enabled=1',
                    (ctx['id'],)).fetchone() if ctx else None
                if (not ctx or ctx['id'] != pending['user_id']
                        or digest(request.cookies.get('ticket_session','')) != pending['session_hash']
                        or not user or user['password_hash'] != pending['password_hash']):
                    pending = None
            elif pending and ctx:
                pending = None
        return JSONResponse(dict(ok=True, enabled=bool(cfg), authenticated=bool(ctx),
            bound=bool(binding), binding=dict(binding) if binding else None, csrf_token=csrf(a,request),
            pending_intent=pending['intent'] if pending else None,
            pending_binding={'name':pending['name']} if pending and pending['intent'] == 'bind' else None),
            headers={'Cache-Control':'no-store'})

    @a.app.get('/api/auth/feishu/start')
    def start(request: Request):
        cfg = required_config()
        if a.session_context(request.cookies.get('ticket_session')):
            fail('ALREADY_AUTHENTICATED',409)
        return new_flow(a,request,'login',cfg)

    @a.app.post('/api/auth/feishu/bind-start')
    def bind_start(payload: Reauth, request: Request):
        cfg = mutation(a,request)
        ctx = a.current_context(request)
        password = reauth(a,request,payload,ctx)
        with a.db() as c:
            if c.execute('SELECT 1 FROM feishu_bindings WHERE user_id=?',(ctx['id'],)).fetchone():
                fail('ALREADY_BOUND',409)
        return new_flow(a,request,'bind',cfg,ctx,password)

    @a.app.get('/api/auth/feishu/callback')
    def callback(request: Request, state: str = '', code: str = '', error: str = ''):
        cfg = required_config()
        with a.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute("SELECT * FROM feishu_oauth WHERE state_hash=? AND correlation_hash=? AND stage='started' AND expires>?",
                (digest(state),digest(request.cookies.get(COOKIE,'')),time.time())).fetchone()
            if not row:
                fail('OAUTH_STATE_INVALID')
            c.execute("UPDATE feishu_oauth SET stage='consumed' WHERE state_hash=?",(row['state_hash'],))
        if error or not code or row['app_id'] != cfg['app_id'] or row['tenant_key'] != cfg['tenant'] or row['redirect_uri'] != cfg['callback']:
            fail('OAUTH_REJECTED')
        try:
            identity = exchange_identity(cfg,row,code)
        except Exception:
            fail('FEISHU_UNAVAILABLE',502)
        if not isinstance(identity,dict) or identity.get('tenant_key') != cfg['tenant'] or not isinstance(identity.get('open_id'),str) or not identity['open_id'] or len(identity['open_id']) > 200:
            fail('FEISHU_IDENTITY_REJECTED',403)
        with a.db() as c:
            c.execute("UPDATE feishu_oauth SET stage='verified',open_id=?,name=?,verifier='' WHERE state_hash=? AND stage='consumed' AND expires>?",
                (identity['open_id'],str(identity.get('name',''))[:120],row['state_hash'],time.time()))
        # A 303 chain from Feishu may still omit the Strict main cookie. Finish
        # loading a same-origin document before initiating the frontend navigation.
        target = '/?feishu=confirm' if row['intent']=='bind' else '/login?feishu=complete'
        nonce = secrets.token_urlsafe(24)
        return HTMLResponse('<!doctype html><html><head><meta charset="utf-8"><title>Feishu sign-in</title></head><body>'
            + '<a href="' + target + '">Continue securely</a><script nonce="' + nonce + '">location.replace('
            + json.dumps(target) + ')</script></body></html>',headers={
                'Cache-Control':'no-store','Referrer-Policy':'no-referrer',
                'Content-Security-Policy':"default-src 'none'; script-src 'nonce-"+nonce+"'; base-uri 'none'; frame-ancestors 'none'"})

    @a.app.post('/api/auth/feishu/unlink')
    def unlink(payload: Reauth, request: Request):
        mutation(a,request)
        ctx = a.current_context(request)
        reauth(a,request,payload,ctx)
        with a.db() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('DELETE FROM feishu_bindings WHERE user_id=?',(ctx['id'],))
            c.execute('DELETE FROM feishu_oauth WHERE user_id=?',(ctx['id'],))
            c.execute('UPDATE feishu_subscriptions SET enabled=0,generation=?,updated_at=? WHERE user_id=?',(str(uuid.uuid4()),time.time(),ctx['id']))
            cancel_jobs(c,ctx['id'])
            audit(c,ctx['id'],'unlink')
        return {'ok':True}

    @a.app.post('/api/auth/feishu/finalize')
    def finalize(payload: Confirm, request: Request):
        cfg = mutation(a,request)
        if payload.confirm is not True:
            fail('CONFIRMATION_REQUIRED')
        ctx = a.session_context(request.cookies.get('ticket_session'))
        with a.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute("SELECT * FROM feishu_oauth WHERE correlation_hash=? AND stage='verified' AND expires>?",
                (digest(request.cookies.get(COOKIE,'')),time.time())).fetchone()
            if not row or row['app_id'] != cfg['app_id'] or row['tenant_key'] != cfg['tenant'] or row['redirect_uri'] != cfg['callback']:
                fail('OAUTH_STATE_INVALID')
            if row['intent'] == 'bind':
                if not ctx or ctx['id'] != row['user_id'] or digest(request.cookies.get('ticket_session','')) != row['session_hash']:
                    fail('ORIGINAL_SESSION_REQUIRED',403)
                user = c.execute('SELECT password_hash FROM users WHERE id=? AND enabled=1',(ctx['id'],)).fetchone()
                if not user or user['password_hash'] != row['password_hash']:
                    fail('REAUTH_REQUIRED',403)
                try:
                    c.execute('INSERT INTO feishu_bindings VALUES(?,?,?,?,?,?,?)',
                        (ctx['id'],cfg['app_id'],cfg['tenant'],row['open_id'],row['name'],str(uuid.uuid4()),time.time()))
                except sqlite3.IntegrityError:
                    fail('BINDING_CONFLICT',409)
                audit(c,ctx['id'],'bind')
                response = JSONResponse({'ok':True,'intent':'bind'})
            else:
                if ctx:
                    fail('ALREADY_AUTHENTICATED',409)
                binding = c.execute('''SELECT b.user_id,b.generation,m.workspace_id FROM feishu_bindings b
                    JOIN users u ON u.id=b.user_id AND u.enabled=1
                    JOIN workspace_memberships m ON m.user_id=u.id
                    WHERE b.app_id=? AND b.tenant_key=? AND b.open_id=?
                    ORDER BY CASE m.workspace_id WHEN 'geekforest' THEN 0 ELSE 1 END LIMIT 1''',
                    (cfg['app_id'],cfg['tenant'],row['open_id'])).fetchone()
                if not binding:
                    fail('EXISTING_BINDING_REQUIRED',403)
                response = JSONResponse({'ok':True,'intent':'login'})
                response.set_cookie('ticket_session',a.session_token(binding['user_id'],binding['workspace_id'],int(time.time())+a.SESSION_TTL,binding['generation']),
                    max_age=a.SESSION_TTL,httponly=True,secure=True,samesite='strict',path='/')
                audit(c,binding['user_id'],'login')
            c.execute('DELETE FROM feishu_oauth WHERE state_hash=?',(row['state_hash'],))
        response.delete_cookie(COOKIE,path='/',secure=True,httponly=True,samesite='lax')
        response.headers['Cache-Control'] = 'no-store'
        return response
