# Deployment guide

## GitHub Actions secrets

Configure these repository secrets for automated deployment:

- `TICKET_DEPLOY_HOST`: production hostname or IP
- `TICKET_DEPLOY_USER`: restricted deployment user, not a shared human account
- `TICKET_DEPLOY_SSH_KEY`: private SSH key for that deployment user
- `TICKET_DEPLOY_PORT`: optional, defaults to `22`

After the secrets and restricted server account are verified, set repository variable `TICKET_AUTO_DEPLOY=true` to deploy automatically after successful CI on `main`. Until then, the deployment workflow can only be started manually and automatic runs stay skipped.

The deployment user needs permission to write `/opt/ticket-system`, create backups under `/opt/ticket-system-backups`, and run only `systemctl restart ticket-system` and `systemctl is-active ticket-system` through sudo.

## What deployment preserves

The workflow does not upload or delete:

- `/etc/ticket-system.env`
- `/opt/ticket-system/data/`
- local `.env` files
- mailbox configuration files
- virtual environments

Before switching code, it snapshots the current application files and SQLite database. The service is restarted only after remote Python and JavaScript syntax checks pass.

## Manual rollback

Select the most recent directory under `/opt/ticket-system-backups/`, restore application files and `tickets.db`, then restart `ticket-system.service`. Confirm service state and `https://ticket.geekforest.ai` after rollback.

## Production checklist

1. Pull request approved and CI green.
2. Database and changed files backed up.
3. No secret or runtime data in the commit.
4. Service active after restart.
5. Public HTTPS login page reachable.
6. Representative ticket list/detail flow verified.
7. Commit hash and backup directory recorded.
