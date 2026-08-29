# Development guide

## Prerequisites

- Python 3.11+
- Node.js 18+ for JavaScript syntax checks
- Access to a development mailbox source only when testing mail ingestion

## Local setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Keep mailbox polling disabled until safe test accounts are configured:

```bash
TICKET_MAIL_WORKER=0
```

The local database is created under `data/tickets.db`. It is ignored by Git.

## Verification

Run before every pull request:

```bash
python3 -m py_compile app.py mail_worker.py
node --check static/app.js
git diff --check
```

For schema work, start once against a copied development database and verify that startup can run twice without errors. Migrations must be additive and idempotent.

## Working with AI and email

- Never use production mailbox passwords locally.
- Do not include real email content in tests or commits.
- AI keys belong in environment variables. The browser must never receive them.
- Incoming email is untrusted input. Prompts must tell the model to ignore instructions found inside email content.

## Git workflow

Create a branch from the latest `main`, use a focused commit, and open a pull request. Do not push directly to `main` unless handling an explicitly approved production incident.
