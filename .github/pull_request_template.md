## Summary

Describe the user-visible outcome and why the change is needed.

## Scope

- [ ] Backend/API
- [ ] Frontend/UI
- [ ] Database migration
- [ ] Mail ingestion/sending
- [ ] AI behavior
- [ ] Documentation/operations

## Verification

- [ ] `python3 -m py_compile app.py mail_worker.py`
- [ ] `node --check static/app.js`
- [ ] `git diff --check`
- [ ] No credentials, `.env`, database, mailbox config, or private email content committed
- [ ] Workspace isolation verified

## Deployment and rollback

State whether deployment is required, what must be backed up, and how to roll back safely.
