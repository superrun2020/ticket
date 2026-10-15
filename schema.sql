CREATE TABLE IF NOT EXISTS mailboxes (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, color TEXT NOT NULL, created_at TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1, workspace_id TEXT NOT NULL DEFAULT 'geekforest', mailbox_tag TEXT NOT NULL DEFAULT '未分类'
);
CREATE TABLE IF NOT EXISTS tickets (
  id TEXT PRIMARY KEY, subject TEXT NOT NULL, customer_name TEXT NOT NULL, customer_email TEXT NOT NULL,
  mailbox_id TEXT NOT NULL REFERENCES mailboxes(id), status TEXT NOT NULL CHECK(status IN ('open','pending','resolved')),
  priority TEXT NOT NULL CHECK(priority IN ('normal','high','urgent')), assignee TEXT NOT NULL,
  ai_category TEXT NOT NULL DEFAULT '待分类', ai_category_status TEXT NOT NULL DEFAULT 'pending',
  ai_category_confidence REAL, ai_category_reason TEXT, ai_category_source TEXT NOT NULL DEFAULT 'ai', ai_classified_at TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tickets_status_updated ON tickets(status, updated_at DESC);
CREATE TABLE IF NOT EXISTS ai_category_feedback (
  id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES tickets(id), workspace_id TEXT NOT NULL,
  previous_category TEXT NOT NULL, corrected_category TEXT NOT NULL,
  subject_snapshot TEXT NOT NULL, body_snapshot TEXT NOT NULL,
  actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_category_feedback_workspace_time ON ai_category_feedback(workspace_id, created_at DESC);
CREATE TABLE IF NOT EXISTS admob_policy_alerts (
  ticket_id TEXT PRIMARY KEY REFERENCES tickets(id), pub_id TEXT NOT NULL, package_name TEXT NOT NULL,
  project_code TEXT NOT NULL DEFAULT '', project_name TEXT NOT NULL DEFAULT '', match_status TEXT NOT NULL,
  notification_sent_at TEXT, notification_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_admob_policy_alerts_sent ON admob_policy_alerts(notification_sent_at, updated_at);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES tickets(id), direction TEXT NOT NULL CHECK(direction IN ('inbound','outbound')),
  sender_name TEXT NOT NULL, sender_email TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0,
  provider_message_id TEXT UNIQUE, internet_message_id TEXT, references_header TEXT,
  inbound_to_emails TEXT, inbound_cc_emails TEXT, inbound_reply_to_emails TEXT,
  delivery_status TEXT NOT NULL DEFAULT 'received', sent_at TEXT, delivered_at TEXT, opened_at TEXT, failed_at TEXT,
  open_count INTEGER NOT NULL DEFAULT 0, delivery_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_ticket_time ON messages(ticket_id, created_at);
CREATE TABLE IF NOT EXISTS attachments (
  id TEXT PRIMARY KEY, message_id TEXT REFERENCES messages(id), outbox_id TEXT REFERENCES outbox(id),
  ticket_id TEXT NOT NULL REFERENCES tickets(id), direction TEXT NOT NULL CHECK(direction IN ('inbound','outbound')),
  filename TEXT NOT NULL, content_type TEXT NOT NULL, size INTEGER NOT NULL,
  storage_path TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attachments_message ON attachments(message_id);
CREATE INDEX IF NOT EXISTS idx_attachments_outbox ON attachments(outbox_id);
CREATE INDEX IF NOT EXISTS idx_attachments_ticket ON attachments(ticket_id, created_at);
CREATE TABLE IF NOT EXISTS outbox (
  id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES tickets(id), to_email TEXT NOT NULL,
  subject TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('queued','sending','sent','failed')),
  created_at TEXT NOT NULL, updated_at TEXT, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
  internet_message_id TEXT, in_reply_to TEXT, references_header TEXT, message_id TEXT,
  to_emails TEXT, cc_emails TEXT, bcc_emails TEXT,
  tracking_token TEXT, delivered_at TEXT, opened_at TEXT, open_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_outbox_status_created ON outbox(status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_tracking_token ON outbox(tracking_token) WHERE tracking_token IS NOT NULL;
CREATE TABLE IF NOT EXISTS mailbox_sync (
  mailbox_id TEXT PRIMARY KEY REFERENCES mailboxes(id), last_uid INTEGER NOT NULL DEFAULT 0,
  backfill_active INTEGER NOT NULL DEFAULT 0, backfill_target_uid INTEGER,
  last_backfill_at TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL, is_secret INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS managed_google_mailboxes (
  id TEXT PRIMARY KEY, project_code TEXT NOT NULL, mailbox_email TEXT NOT NULL UNIQUE,
  password_ciphertext TEXT NOT NULL, mailbox_tag TEXT NOT NULL DEFAULT '未分类',
  workspace_id TEXT NOT NULL DEFAULT 'geekforest', enabled INTEGER NOT NULL DEFAULT 1,
  created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_managed_google_workspace ON managed_google_mailboxes(workspace_id, enabled);
CREATE TABLE IF NOT EXISTS mail_service_domains (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1, dns_zone_file TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'stalwart', synced_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mail_service_accounts (
  id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL DEFAULT '',
  domain_name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL DEFAULT 'stalwart', synced_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mail_service_accounts_domain ON mail_service_accounts(domain_name, enabled);
CREATE TABLE IF NOT EXISTS managed_stalwart_mailboxes (
  id TEXT PRIMARY KEY, account_id TEXT NOT NULL UNIQUE, mailbox_email TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL DEFAULT '', password_ciphertext TEXT NOT NULL,
  mailbox_tag TEXT NOT NULL DEFAULT '未分类', workspace_id TEXT NOT NULL DEFAULT 'geekforest',
  enabled INTEGER NOT NULL DEFAULT 1, created_by TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_managed_stalwart_workspace ON managed_stalwart_mailboxes(workspace_id, enabled);
CREATE TABLE IF NOT EXISTS workspaces (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspace_memberships (
  user_id TEXT NOT NULL REFERENCES users(id), workspace_id TEXT NOT NULL REFERENCES workspaces(id),
  role TEXT NOT NULL CHECK(role IN ('admin','member')), created_at TEXT NOT NULL,
  PRIMARY KEY(user_id, workspace_id)
);
CREATE INDEX IF NOT EXISTS idx_memberships_workspace ON workspace_memberships(workspace_id);

-- Feishu is additive: no users, memberships or subscriptions are seeded.
CREATE TABLE IF NOT EXISTS feishu_bindings (
 user_id TEXT PRIMARY KEY REFERENCES users(id), app_id TEXT NOT NULL, tenant_key TEXT NOT NULL,
 open_id TEXT NOT NULL, name TEXT NOT NULL, generation TEXT NOT NULL UNIQUE, bound_at REAL NOT NULL,
 UNIQUE(app_id,tenant_key,open_id)
);
CREATE TABLE IF NOT EXISTS feishu_oauth (
 state_hash TEXT PRIMARY KEY, correlation_hash TEXT NOT NULL UNIQUE, intent TEXT NOT NULL,
 user_id TEXT, session_hash TEXT, password_hash TEXT, app_id TEXT NOT NULL, tenant_key TEXT NOT NULL,
 redirect_uri TEXT NOT NULL, verifier TEXT NOT NULL, expires REAL NOT NULL, stage TEXT NOT NULL,
 open_id TEXT, name TEXT
);
CREATE INDEX IF NOT EXISTS idx_feishu_oauth_expiry ON feishu_oauth(expires);
CREATE TABLE IF NOT EXISTS feishu_subscriptions (
 user_id TEXT NOT NULL REFERENCES users(id), mailbox_id TEXT NOT NULL REFERENCES mailboxes(id),
 enabled INTEGER NOT NULL DEFAULT 0, generation TEXT NOT NULL, updated_at REAL NOT NULL,
 PRIMARY KEY(user_id,mailbox_id)
);
CREATE TABLE IF NOT EXISTS feishu_notification_outbox (
 id TEXT PRIMARY KEY, message_id TEXT NOT NULL REFERENCES messages(id), ticket_id TEXT NOT NULL REFERENCES tickets(id),
 mailbox_id TEXT NOT NULL, workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
 app_id TEXT NOT NULL, tenant_key TEXT NOT NULL, open_id TEXT NOT NULL, canonical_base TEXT NOT NULL,
 binding_generation TEXT NOT NULL, subscription_generation TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0, lease_token TEXT,
 first_attempt REAL, vendor_message_id TEXT, last_error TEXT, created_at REAL NOT NULL,
 UNIQUE(message_id,user_id)
);
CREATE INDEX IF NOT EXISTS idx_feishu_jobs_due ON feishu_notification_outbox(status,next_attempt,lease_until);
CREATE TABLE IF NOT EXISTS feishu_audit (
 id TEXT PRIMARY KEY, user_id TEXT, action TEXT NOT NULL, created_at REAL NOT NULL
);
