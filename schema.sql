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
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES tickets(id), direction TEXT NOT NULL CHECK(direction IN ('inbound','outbound')),
  sender_name TEXT NOT NULL, sender_email TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0,
  provider_message_id TEXT UNIQUE, internet_message_id TEXT, references_header TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_ticket_time ON messages(ticket_id, created_at);
CREATE TABLE IF NOT EXISTS outbox (
  id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES tickets(id), to_email TEXT NOT NULL,
  subject TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('queued','sending','sent','failed')),
  created_at TEXT NOT NULL, updated_at TEXT, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
  internet_message_id TEXT, in_reply_to TEXT, references_header TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_status_created ON outbox(status, created_at);
CREATE TABLE IF NOT EXISTS mailbox_sync (
  mailbox_id TEXT PRIMARY KEY REFERENCES mailboxes(id), last_uid INTEGER NOT NULL DEFAULT 0,
  backfill_active INTEGER NOT NULL DEFAULT 0, backfill_target_uid INTEGER,
  last_backfill_at TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS managed_google_mailboxes (
  id TEXT PRIMARY KEY, project_code TEXT NOT NULL, mailbox_email TEXT NOT NULL UNIQUE,
  password_ciphertext TEXT NOT NULL, mailbox_tag TEXT NOT NULL DEFAULT '未分类',
  workspace_id TEXT NOT NULL DEFAULT 'geekforest', enabled INTEGER NOT NULL DEFAULT 1,
  created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_managed_google_workspace ON managed_google_mailboxes(workspace_id, enabled);
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
