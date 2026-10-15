# Feishu SSO and private notifications — frontend contract

Feature is disabled unless `TICKET_FEISHU_APP_ID`, `TICKET_FEISHU_APP_SECRET`, `TICKET_FEISHU_TENANT_KEY`, and `TICKET_PUBLIC_BASE_URL` (canonical HTTPS origin, no path) are set. Worker additionally requires `TICKET_FEISHU_NOTIFY_WORKER=1` (default 0). Intended app: `cli_aa5ac03a52f89c2c`; intended origin: `https://ticket.geekforest.ai`. Neither secret nor tenant is known/verified here.

All JSON errors use FastAPI `detail.error`. All responses must be treated as no-store. Main `ticket_session` remains Secure/HttpOnly/SameSite=Strict. Separate `ticket_feishu_oauth` cookie is Secure/HttpOnly/Lax, 10 minutes.

## Browser API

- `GET /api/auth/feishu/status` is public: `{ok, enabled, authenticated, bound, csrf_token, pending_intent, pending_binding}`. Authenticated response includes `binding: {name, bound_at}` or null; never exposes open_id. `pending_intent` is `login`, `bind`, or null for a verified callback awaiting same-origin finalization. `pending_binding` is `{name: string}` only for a verified, unexpired bind flow matching the current OAuth correlation cookie, configured app/tenant/callback, and exact original enabled account/session with unchanged password; otherwise null. Login flows never expose a pending name. Both pending fields are null for invalid context. The name comes only from server-verified callback state, never query parameters; it is display-only untrusted text (render with `textContent`). Show this pending name and explicit confirmation before enabling bind finalization, not `binding.name`. Status neither consumes the flow nor replaces finalization's authorization checks.
- `GET /api/auth/feishu/start`: login-only OAuth redirect; not bind. Navigate top-level. Existing logged-in sessions are rejected. No arbitrary redirect URL accepted.
- `POST /api/auth/feishu/bind-start`: authenticated JSON `{password, confirm:true}`; returns `{ok, authorization_url}`. Browser navigates to this URL. Reauthentication must be the current original account's password. Cannot silently replace a binding.
- `GET /api/auth/feishu/callback`: OAuth-only callback; consumes state and verifies identity server-side. Returns **200 HTML same-origin bridge** (nonce CSP, no secrets) which initiates a fresh navigation to `/login?feishu=complete` for login, `/?feishu=confirm` for binding, with a manual Continue link fallback. Do not replace this with a cross-site 303 chain: Strict cookies can remain absent throughout that chain. Does NOT issue a session or bind yet.
- On those query parameters fetch status, then `POST /api/auth/feishu/finalize` JSON `{confirm:true}`. For bind show explicit confirmation first. Login can finalize after status automatically. Returns `{ok, intent}`; then navigate `/`. Bind revalidates exact original authenticated session plus original-account identity. Never use OAuth parameters as identity.
- `POST /api/auth/feishu/unlink`: authenticated JSON `{password, confirm:true}`. Cancels subscriptions/jobs and revokes Feishu-issued sessions; password sessions keep existing policy. A Feishu-session caller will subsequently need password login.
- `GET /api/notifications/feishu/subscriptions`: authenticated `{ok, items:[{mailbox_id,name,workspace_id,enabled}]}`; only enabled mailboxes in current workspace, default disabled. `enabled` means opted in.
- `PUT /api/notifications/feishu/subscriptions/{mailbox_id}` JSON `{enabled:boolean}`. Requires verified binding and authorized enabled mailbox in current workspace. Opt-out cancels jobs; opt-in does not replay history.
- `POST /api/notifications/feishu/open-ticket` JSON `{ticket_id}`: verifies membership for ticket's actual workspace before switching session; returns `{ok,ticket_id,workspace_id}`. Bot link is canonical `/?ticket=<id>`; unauthenticated middleware preserves the validated ticket ID as `/login?ticket=<id>`. Frontend must capture that ID in same-tab sessionStorage before navigating away for OAuth, restore it after successful password/Feishu login, and invoke this endpoint before fetching ticket. Do not switch workspace from untrusted query parameters.

Every POST/PUT above requires exact `Origin: <TICKET_PUBLIC_BASE_URL>` and `X-Feishu-CSRF: <csrf_token from status>`; token is bound to current session (or OAuth correlation for unauthenticated login finalization). Refresh status after authentication/session switch. No wildcard origins, no recipient parameters, no admin-only privilege exemption.

## Error and field schema

Successful JSON operations return HTTP 200. Start returns 303 with `Location`; callback returns 200 HTML. `status` booleans are actual JSON booleans; `binding.bound_at` is Unix seconds (number); `binding.name` is display-only untrusted text and must be escaped. Subscription list `enabled` is SQLite integer 0/1 (coerce with `Boolean`); mutation `enabled` is boolean. Successful subscription mutation returns `{ok:true,mailbox_id:string,enabled:boolean}`; unlink returns `{ok:true}`. No endpoint accepts local user IDs, tenant IDs, open IDs, recipient IDs, app secrets, or return URLs from the frontend.

Domain failures are `{"detail":{"error":"CODE"}}`; protected-route middleware may instead return `{"ok":false,"error":"AUTH_REQUIRED"}`. Validation failure is FastAPI 422 with `detail` validation array. Handle all three without printing raw server responses.

| HTTP | Codes | Frontend action |
|---|---|---|
| 400 | `CONFIRMATION_REQUIRED`, `OAUTH_STATE_INVALID`, `OAUTH_REJECTED` | Show safe error; restart flow for expired/replayed/missing correlation or denied consent. |
| 401 | `AUTH_REQUIRED`, `INVALID_CREDENTIALS` | Password login / password reauthentication. |
| 403 | `ORIGIN_REJECTED`, `CSRF_REJECTED` | Refresh status from same origin, do not automatically repeat mutation. |
| 403 | `FEISHU_IDENTITY_REJECTED` | Wrong tenant or missing verified identity; no binding/login. |
| 403 | `ORIGINAL_SESSION_REQUIRED`, `REAUTH_REQUIRED` | Original session/account/password changed or disappeared; restart binding from original account. |
| 403 | `EXISTING_BINDING_REQUIRED` | Sign in with existing password account and explicitly bind. No account is created. |
| 403 | `MAILBOX_FORBIDDEN`, `TICKET_FORBIDDEN` | Stop; do not switch workspace or load ticket. |
| 409 | `ALREADY_AUTHENTICATED`, `ALREADY_BOUND`, `BINDING_CONFLICT` | Keep current identity; never silently replace. |
| 429 | `TOO_MANY_ATTEMPTS` | Reauthentication limit: 10 failures per local account in 15 minutes. |
| 502 | `FEISHU_UNAVAILABLE` | Provider exchange failed; restart OAuth rather than replaying callback. |
| 503 | `FEISHU_DISABLED` | Hide/disable sign-in and subscription mutation affordances. |

`POST /api/mail/incoming` now enforces session workspace mailbox authorization and exact browser Origin; trusted workers continue to call `receive_mail` directly. Cross-mailbox provider-ID collisions fail 409 `MESSAGE_SOURCE_CONFLICT` without exposing the foreign ticket ID. Reply parent IDs only attach within the receiving mailbox.

## Configuration and delivery

Configure callback `<origin>/api/auth/feishu/callback` in Feishu. Enable/publish self-built bot and grant `im:message:send_as_bot` with intended users in availability scope. OAuth uses v3 token exchange + S256 PKCE, then user_info tenant/open_id; no email matching, user creation or permission grants. Tokens are discarded, never returned to browser.

Notification outbox is separate from mail outbox. Only new nonhistorical inbound insertions enqueue, inside the ingestion transaction. Private text contains ticket ID and authenticated canonical link, not subject/body/attachments. Jobs snapshot binding and subscription generations. Worker rechecks authorization immediately before each bounded send. Stable UUID (36 chars) is reused; vendor dedupe is only one hour, so ambiguous jobs terminate before that window instead of retrying indefinitely. Sent message_id is stored; this is API acceptance, not proof of read/delivery. No live vendor integration has been verified.
