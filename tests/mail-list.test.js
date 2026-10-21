const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const appSource = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const indexSource = fs.readFileSync(require.resolve('../static/index.html'), 'utf8');
const backendSource = fs.readFileSync(require.resolve('../app.py'), 'utf8');
const listRouteStart = backendSource.indexOf('@app.get("/api/tickets")');
const listRouteEnd = backendSource.indexOf('@app.get("/api/mail/inbound-cursor")', listRouteStart);
const listRoute = backendSource.slice(listRouteStart, listRouteEnd);

test('mail list is metadata-only and delegates clicks to the detail loader', () => {
  assert.ok(listRouteStart >= 0 && listRouteEnd > listRouteStart);
  assert.doesNotMatch(listRoute, /SELECT body FROM messages/);
  assert.match(appSource, /data-ticket-id=/);
  assert.match(appSource, /function bindTicketListInteractions\(\)/);
  assert.match(appSource, /list\.addEventListener\('click',openFromEvent\)/);
});

test('delegated mail-list click opens a ticket after the list is redrawn', () => {
  let listener;
  let opened;
  const card = {dataset: {ticketId: 'TKT-42'}};
  const list = {
    dataset: {},
    contains: node => node === card,
    addEventListener(type, handler) { if (type === 'click') listener = handler; },
  };
  const target = {closest: () => card};
  const context = {
    $: selector => selector === '#ticketList' ? list : null,
    openTicket: id => { opened = id; return Promise.resolve(); },
    toast: () => {},
  };
  vm.createContext(context);
  const start = appSource.indexOf('function bindTicketListInteractions(){');
  const end = appSource.indexOf("document.querySelectorAll('[data-mail-folder]')", start);
  assert.ok(start >= 0 && end > start);
  vm.runInContext(appSource.slice(start, end), context);
  context.bindTicketListInteractions();
  listener({target, preventDefault() {}});
  assert.equal(opened, 'TKT-42');
});

test('mail navigation exposes a mailbox selector and cache-busted app script', () => {
  assert.match(indexSource, /id="mailboxQuickSwitcher"/);
  assert.match(indexSource, /邮箱列表/);
  assert.match(indexSource, /app\.js\?v=20261021-mailbox-client-config-1/);
  assert.match(appSource, /quickSwitcher\.onchange=event=>\{void selectMailbox\(event\.target\.value,quickSwitcher\)\}/);
  assert.match(indexSource, /id="mailboxClientConfigNav"/);
  assert.match(appSource, /\/api\/mailboxes\/\$\{encodeURIComponent\(mailboxId\)\}\/client-config/);
  assert.match(appSource, /一键复制全部信息/);
});

test('workspace changes clear the previous mailbox filter before reloading', () => {
  assert.match(appSource, /applySession\(actualSession\);state\.mailbox=state\.displayMode==='ticket'\?'all':\(actualSession\.default_mailbox_id\|\|'all'\);state\.mailFolder='inbox';/);
});

test('mail list is paginated and can continue loading older rows', () => {
  assert.match(listRoute, /limit: int = 100, offset: int = 0/);
  assert.match(listRoute, /LIMIT \? OFFSET \?/);
  assert.match(listRoute, /"has_more": has_more/);
  assert.match(appSource, /loadMoreTickets\(button\)/);
  assert.match(appSource, /id="loadMoreTickets"/);
});

test('session exposes the default mailbox so the first list request is scoped', () => {
  assert.match(backendSource, /default_mailbox_id/);
  assert.match(appSource, /state\.mailbox=state\.displayMode==='ticket'\?'all':\(d\.default_mailbox_id\|\|'all'\)/);
});
