# AGENTS.md

This repository contains the GeekForest production ticket system.

## Required context

- Read `README.md` and `docs/DEVELOPMENT.md` before changing code.
- For GeekForest engineering, API, database, security, UI, or deployment work, use the shared enterprise skill at `/Users/oliver/.codex/skills/enterprise-global` when it is available.
- Preserve existing architecture: FastAPI backend, vanilla JavaScript frontend, SQLite ticket persistence, and MySQL mailbox-source integrations.

## Safety rules

- Never commit `.env`, mailbox passwords, database credentials, API keys, SSH keys, cookies, or `data/tickets.db`.
- Never delete or replace production tickets, messages, mailboxes, outbox rows, or sync cursors.
- Treat `GeekForest` and `Eddy Personal` as isolated workspaces. Do not move records between them without an explicit migration.
- Database migrations must be additive and safe to rerun. Back up the production database before deployment.
- Do not enable a mailbox source or historical backfill unless the task explicitly requires it.
- Do not expose arbitrary SQL APIs. Use narrow, validated endpoints with server-side authorization and audit logs.

## Delivery workflow

1. Inspect `git status` and preserve unrelated changes.
2. Make scoped edits using existing project patterns.
3. Run:
   - `python3 -m py_compile app.py mail_worker.py`
   - `node --check static/app.js`
   - `git diff --check`
4. Review staged filenames for secrets and generated data.
5. Open a pull request. Production deployment should run only from reviewed `main`.
6. Verify the systemd service and public HTTPS endpoint after deployment.
7. Report changed files, tests, deployment result, rollback location, and commit hash.

## Production reference

- Application directory: `/opt/ticket-system`
- Environment file: `/etc/ticket-system.env`
- Service: `ticket-system.service`
- Public URL: `https://ticket.geekforest.ai`
- Backups: `/opt/ticket-system-backups/`

Never place production credentials in this file or any repository documentation.
