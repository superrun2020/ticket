const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');

test('mailbox URL tracks the selected mailbox and removes it for the all-mail view', () => {
  const calls = [];
  const context = {
    URL,
    state: {
      mailbox: 'box-1',
      mailboxes: [{ id: 'box-1', email: 'team@example.com' }],
      session: { user: { workspace_id: 'workspace-1' } },
    },
    window: {
      location: { href: 'https://ticket.geekforest.ai/' },
      history: {
        replaceState(_state, _title, url) {
          calls.push(['replace', String(url)]);
          context.window.location.href = String(url);
        },
        pushState(_state, _title, url) {
          calls.push(['push', String(url)]);
          context.window.location.href = String(url);
        },
      },
    },
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('let restoringMailboxUrl'), source.indexOf('async function loadSession()')), context);

  context.updateMailboxUrl();
  let url = new URL(context.window.location.href);
  assert.equal(url.searchParams.get('mailbox'), 'team@example.com');
  assert.equal(url.searchParams.get('workspace'), 'workspace-1');

  context.state.mailbox = 'all';
  context.updateMailboxUrl();
  url = new URL(context.window.location.href);
  assert.equal(url.searchParams.has('mailbox'), false);
  assert.equal(url.searchParams.has('workspace'), false);
  assert.equal(calls.length, 2);
});

test('selected mailbox refresh triggers its sync endpoint then reloads the list', async () => {
  const requests = [];
  let loads = 0;
  const toasts = [];
  const context = {
    encodeURIComponent,
    state: { mailbox: 'box/1', session: { user: { workspace_id: 'workspace-1' } } },
    workspaceGeneration: 2,
    api: async (url, options) => { requests.push({ url, options }); return { ok: true }; },
    load: async () => { loads += 1; },
    toast: message => toasts.push(message),
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('async function refreshSelectedMailbox('), source.indexOf('function renderNav(')), context);
  const button = {
    disabled: false,
    attrs: {},
    setAttribute(name, value) { this.attrs[name] = value; },
    removeAttribute(name) { delete this.attrs[name]; },
  };

  assert.equal(await context.refreshSelectedMailbox(button), true);
  assert.equal(JSON.stringify(requests), JSON.stringify([{ url: '/api/mailboxes/box%2F1/sync', options: { method: 'POST' } }]));
  assert.equal(loads, 1);
  assert.deepEqual(toasts, ['邮箱收取完成']);
  assert.equal(button.disabled, false);
  assert.equal(button.attrs['aria-busy'], undefined);
});

test('deep links restore the mailbox by email and sync it on initial open', () => {
  assert.match(source, /params\.get\('mailbox'\)/);
  assert.match(source, /params\.get\('workspace'\)/);
  assert.match(source, /await refreshSelectedMailbox\(\$\('#refreshTickets'\)\)/);
});
