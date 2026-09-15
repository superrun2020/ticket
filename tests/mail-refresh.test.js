const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {createMailRefreshController, createAlertPreferences} = require('../static/mail-refresh.js');

const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return {promise, resolve} };

test('baselines silently, polls only while visible, and refreshes immediately on return', async () => {
  let visible = true, intervalCallback, listLoads = 0;
  const cursorCalls = [];
  const controller = createMailRefreshController({
    getWorkspace: () => 'alpha', isVisible: () => visible,
    fetchCursor: async since => { cursorCalls.push(since); return {cursor: cursorCalls.length, messages: since == null ? [] : [{id: 'reply'}]} },
    refreshList: async () => { listLoads++ }, onMessages: () => {}, onStatus: () => {},
    setIntervalFn: callback => { intervalCallback = callback; return 1 }, clearIntervalFn: () => {}
  });
  await controller.start();
  assert.deepEqual(cursorCalls, [null]);
  assert.equal(listLoads, 0);
  await intervalCallback();
  assert.equal(listLoads, 1);
  visible = false;
  await intervalCallback();
  assert.equal(listLoads, 1);
  visible = true;
  await controller.visibilityChanged();
  assert.equal(listLoads, 2);
});

test('prevents overlap and discards a response from the previous workspace', async () => {
  let workspace = 'alpha', notifications = 0, listLoads = 0, refreshedWorkspace = null;
  const slow = deferred();
  const controller = createMailRefreshController({
    getWorkspace: () => workspace, isVisible: () => true,
    fetchCursor: since => since == null ? Promise.resolve({cursor: 1, messages: []}) : slow.promise,
    refreshList: async expectedWorkspace => { listLoads++; refreshedWorkspace = expectedWorkspace }, onMessages: items => { notifications += items.length }, onStatus: () => {},
    setIntervalFn: () => 1, clearIntervalFn: () => {}
  });
  await controller.start();
  const first = controller.refresh('poll');
  const overlap = await controller.refresh('poll');
  assert.equal(overlap, false);
  workspace = 'beta';
  const switched = controller.workspaceChanged();
  slow.resolve({cursor: 2, messages: [{id: 'stale'}]});
  await first;
  await switched;
  assert.equal(notifications, 0);
  assert.equal(listLoads, 0);
  assert.equal(refreshedWorkspace, null);
});

test('pauses stale requests without discarding the source workspace cursor', async () => {
  const seenSince = [], messages = [];
  let workspace = 'alpha';
  const controller = createMailRefreshController({
    getWorkspace: () => workspace, isVisible: () => true,
    fetchCursor: async since => { seenSince.push(since); return {cursor: since == null ? 1 : 2, messages: since == null ? [] : [{id: 'message2'}]}; },
    refreshList: async () => {}, onMessages: items => messages.push(...items), onStatus: () => {},
    setIntervalFn: () => 1, clearIntervalFn: () => {}
  });
  await controller.start();
  controller.pauseWorkspaceSwitch();
  assert.equal(await controller.refresh('poll'), false);
  controller.resumeWorkspace('alpha');
  assert.equal(await controller.refresh('poll'), true);
  assert.deepEqual(seenSince, [null, 1]);
  assert.deepEqual(messages.map(item => item.id), ['message2']);
});

test('successful workspace switch discards the old cursor and silently baselines', async () => {
  const seenSince = [], messages = [];
  let workspace = 'alpha';
  const controller = createMailRefreshController({
    getWorkspace: () => workspace, isVisible: () => true,
    fetchCursor: async since => { seenSince.push(since); return {cursor: seenSince.length, messages: [{id: 'existing'}]}; },
    refreshList: async () => {}, onMessages: items => messages.push(...items), onStatus: () => {},
    setIntervalFn: () => 1, clearIntervalFn: () => {}
  });
  await controller.start();
  controller.pauseWorkspaceSwitch();
  workspace = 'beta';
  await controller.workspaceChanged();
  assert.deepEqual(seenSince, [null, null]);
  assert.deepEqual(messages, []);
});

test('passes the captured workspace to the list loader for stale-payload rejection', async () => {
  let receivedWorkspace;
  const controller = createMailRefreshController({
    getWorkspace: () => 'alpha', isVisible: () => true,
    fetchCursor: async since => ({cursor: since == null ? 1 : 2, messages: []}),
    refreshList: async workspace => { receivedWorkspace = workspace }, onMessages: () => {}, onStatus: () => {},
    setIntervalFn: () => 1, clearIntervalFn: () => {}
  });
  await controller.start();
  await controller.refresh('manual');
  assert.equal(receivedWorkspace, 'alpha');
});

test('persists opt-in sound, unlocks it from the toggle gesture, and requests notifications explicitly', async () => {
  const values = new Map();
  let resumed = 0, tones = 0, permissionRequests = 0, browserNotices = 0;
  const storage = {getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value)};
  const audio = {state: 'suspended', resume: async () => { resumed++ }, playTone: () => { tones++ }};
  const notifications = {permission: 'default', requestPermission: async () => { permissionRequests++; notifications.permission = 'granted'; return 'granted' }, show: () => { browserNotices++ }};
  const preferences = createAlertPreferences({storage, audio, notifications});
  assert.equal(preferences.soundEnabled(), false);
  await preferences.setSoundEnabled(true);
  assert.equal(values.get('ticket-mail-sound'), '1');
  assert.equal(resumed, 1);
  assert.equal(permissionRequests, 0);
  await preferences.requestNotificationPermission();
  preferences.alert({subject: 'Reply'});
  assert.equal(permissionRequests, 1);
  assert.equal(tones, 1);
  assert.equal(browserNotices, 1);
});

test('ancillary alert failures do not fail refresh or prevent cursor progression', async () => {
  let cursorCalls = 0;
  const seenSince = [];
  const phases = [];
  const controller = createMailRefreshController({
    getWorkspace: () => 'alpha', isVisible: () => true,
    fetchCursor: async since => { seenSince.push(since); return {cursor: ++cursorCalls, messages: since == null ? [] : [{id: `m${cursorCalls}`}]}; },
    refreshList: async () => {}, onMessages: () => { throw new Error('Notification constructor failed') },
    onStatus: update => phases.push(update.phase), setIntervalFn: () => 1, clearIntervalFn: () => {}
  });
  await controller.start();
  assert.equal(await controller.refresh('manual'), true);
  assert.equal(await controller.refresh('manual'), true);
  assert.deepEqual(phases, ['loading', 'success', 'loading', 'success']);
  assert.deepEqual(seenSince, [null, 1, 2]);
});

test('alert preferences isolate sound and notification constructor failures', () => {
  const storage = {getItem: () => '1', setItem: () => {}};
  const preferences = createAlertPreferences({
    storage,
    audio: {state: 'running', playTone: () => { throw new Error('Audio failed') }},
    notifications: {permission: 'granted', show: () => { throw new Error('Notification failed') }}
  });
  assert.doesNotThrow(() => preferences.alert({subject: 'Reply'}));
});

test('workbench exposes list-refresh and opt-in alert controls wired to the controller', () => {
  const html = fs.readFileSync(require.resolve('../static/index.html'), 'utf8');
  const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
  assert.match(html, /id="refreshTickets"[^>]*>[^<]*刷新工单列表/);
  assert.match(html, /id="mailSoundToggle"/);
  assert.match(html, /id="notificationPermission"/);
  assert.match(html, /mail-refresh\.js/);
  assert.match(app, /visibilitychange/);
  assert.match(app, /workspaceChanged\(\)/);
  assert.match(app, /refresh\('manual'\)/);
});
