const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const extract = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return {promise, resolve} };

function loadHarness() {
  const requests = [];
  const detail = {innerHTML: 'draft stays'};
  const context = {
    URLSearchParams, state: {status:'all',mailbox:'all',tag:'all',q:'',view:'all',priority:'all',category:'all',sort:'latest',tickets:[],selected:'T1',session:{user:{workspace_id:'A'}}},
    listRequestSequence: 0, workspaceGeneration: 0, refreshController:{pauseWorkspaceSwitch(){}},
    api: url => { const pending = deferred(); requests.push({url, pending}); return pending.promise },
    renderList() {}, renderNav() {}, emptyHtml: () => 'empty', $: selector => selector === '#detail' ? detail : {},
    attachmentState: {reply:[{name:'draft.pdf'}],compose:[]},
  };
  vm.createContext(context);
  vm.runInContext(extract('function beginWorkspaceSwitch(', 'async function loadSession('), context);
  return {context, requests, detail};
}

function switchHarness({postResult='reject', sessionResult='A'}={}) {
  const calls = [], toasts = [], detail = {innerHTML:'draft stays'};
  const selector = {value:'B', disabled:false};
  const elements = new Map();
  const element = key => elements.get(key) || elements.set(key, {innerHTML:'',value:'',textContent:'',hidden:false,onchange:null}).get(key);
  let paused = 0, resumed = 0, changed = 0, loads = 0;
  const context = {
    state:{status:'all',mailbox:'all',tag:'all',q:'',view:'all',priority:'all',category:'all',sort:'latest',tickets:[],selected:'T1',session:{user:{workspace_id:'A'}}}, workspaceGeneration:0, listRequestSequence:0,
    refreshController:{pauseWorkspaceSwitch(){paused++},resumeWorkspace(){resumed++},async workspaceChanged(){changed++}},
    api:async url=>{calls.push(url); if(url.includes('/switch')){if(postResult==='reject')throw new Error('POST_FAILED');return {}} if(url.includes('/tickets?')){loads++;return payload('NEW')} if(sessionResult==='unavailable')throw new Error('SESSION_FAILED');return {user:{workspace_id:sessionResult},workspaces:[{id:'A',name:'A'},{id:'B',name:'B'}]};},
    URLSearchParams, JSON, renderList(){}, renderNav(){}, emptyHtml:()=> 'empty', toast:message=>toasts.push(message), esc:value=>String(value??''), initials:value=>String(value??''), preloadMailService:async()=>{},
    $:key=>key==='#detail'?detail:key==='#workspaceSwitcher'?selector:element(key),
  };
  vm.createContext(context);
  vm.runInContext(extract('function beginWorkspaceSwitch(', 'function showLoadError('), context);
  return {context,selector,detail,calls,toasts,counts:()=>({paused,resumed,changed,loads})};
}

const payload = id => ({tickets:[{id}], mailboxes:[], category_options:[], counts:{}, summary:{}, tag_options:[], category_counts:{}});

test('load accepts only the newest request across filter changes and preserves detail', async () => {
  const {context, requests, detail} = loadHarness();
  context.state.q = 'old'; const oldLoad = context.load();
  context.state.q = 'new'; const newLoad = context.load();
  requests[1].pending.resolve(payload('NEW')); await newLoad;
  requests[0].pending.resolve(payload('OLD')); await oldLoad;
  assert.deepEqual(context.state.tickets.map(x => x.id), ['NEW']);
  assert.match(requests[0].url, /q=old/); assert.match(requests[1].url, /q=new/);
  assert.equal(detail.innerHTML, 'draft stays');
  assert.equal(context.state.selected, 'T1');
  assert.equal(context.attachmentState.reply[0].name, 'draft.pdf');
});

test('load rejects a request invalidated by workspace generation even after A to B to A', async () => {
  const {context, requests} = loadHarness();
  const stale = context.load();
  context.beginWorkspaceSwitch();
  context.state.session.user.workspace_id = 'B';
  context.beginWorkspaceSwitch();
  context.state.session.user.workspace_id = 'A';
  requests[0].pending.resolve(payload('STALE')); await stale;
  assert.deepEqual(context.state.tickets, []);
});

function sidebarRefreshHarness({mailbox='A042A'}={}) {
  const requests = [], toasts = [];
  const button = {disabled:false, textContent:'↻', attributes:{}, setAttribute(name,value){this.attributes[name]=value}, removeAttribute(name){delete this.attributes[name]}};
  const context = {
    URLSearchParams, JSON,
    state:{status:'open',mailbox,tag:'vip',q:'needle',view:'mine',priority:'urgent',category:'billing',sort:'oldest',tickets:[],selected:'T1',session:{user:{workspace_id:'W1'}}},
    listRequestSequence:0, workspaceGeneration:0,
    api:url=>{const pending=deferred();requests.push({url,pending});return pending.promise},
    renderList(){},renderNav(){},toast:message=>toasts.push(message),
  };
  vm.createContext(context);
  vm.runInContext(extract('async function load(', 'function applySession(')+extract('async function refreshSelectedMailbox(', 'function renderNav('), context);
  return {context,requests,toasts,button};
}

test('sidebar refresh requests only the captured mailbox with existing filters', async () => {
  const h=sidebarRefreshHarness();
  const refresh=h.context.refreshSelectedMailbox(h.button);
  assert.equal(h.requests.length,1);
  const params=new URL(h.requests[0].url,'https://example.test').searchParams;
  assert.equal(params.get('mailbox'),'A042A');
  assert.deepEqual(Object.fromEntries(params),{status:'open',mailbox:'A042A',tag:'vip',q:'needle',view:'mine',priority:'urgent',category:'billing',sort:'oldest'});
  assert.equal(h.button.disabled,true);
  h.requests[0].pending.resolve(payload('NEW'));
  await refresh;
  assert.equal(h.button.disabled,false);
  assert.match(h.toasts.at(-1),/A042A.*刷新/);
});

test('sidebar refresh does nothing without a concrete mailbox and prevents duplicate clicks', async () => {
  const none=sidebarRefreshHarness({mailbox:'all'});
  assert.equal(await none.context.refreshSelectedMailbox(none.button),false);
  assert.equal(none.requests.length,0);
  const h=sidebarRefreshHarness();
  const first=h.context.refreshSelectedMailbox(h.button);
  assert.equal(await h.context.refreshSelectedMailbox(h.button),false);
  assert.equal(h.requests.length,1);
  h.requests[0].pending.resolve(payload('NEW'));await first;
});

test('sidebar refresh rejection restores its button', async () => {
  const h=sidebarRefreshHarness();
  const refresh=h.context.refreshSelectedMailbox(h.button);
  h.requests[0].pending.resolve(Promise.reject(new Error('offline')));
  await refresh;
  assert.equal(h.button.disabled,false);
  assert.equal(h.button.attributes['aria-busy'],undefined);
  assert.match(h.toasts.at(-1),/失败/);
});

test('sidebar refresh suppresses stale notification after switching during request', async () => {
  const h=sidebarRefreshHarness();
  const refresh=h.context.refreshSelectedMailbox(h.button);
  h.context.state.mailbox='B055B';
  h.context.listRequestSequence++;
  h.requests[0].pending.resolve(payload('STALE'));
  await refresh;
  assert.deepEqual(h.toasts,[]);
});

test('failed switch reconciles the original workspace, preserves drafts, and resumes polling', async () => {
  const h = switchHarness();
  await h.context.switchWorkspace('B', h.selector);
  assert.deepEqual(h.counts(), {paused:1,resumed:1,changed:0,loads:0});
  assert.equal(h.context.state.session.user.workspace_id, 'A');
  assert.equal(h.detail.innerHTML, 'draft stays');
  assert.equal(h.selector.disabled, false);
});

test('uncertain failed switch reconciles an actual change and rebaselines once', async () => {
  const h = switchHarness({sessionResult:'B'});
  await h.context.switchWorkspace('B', h.selector);
  assert.deepEqual(h.counts(), {paused:1,resumed:0,changed:1,loads:1});
  assert.equal(h.context.state.session.user.workspace_id, 'B');
  assert.equal(h.detail.innerHTML, 'empty');
});

test('unverifiable switch stays paused with visible retry and prevents overlap', async () => {
  const h = switchHarness({sessionResult:'unavailable'});
  const first = h.context.switchWorkspace('B', h.selector);
  const second = await h.context.switchWorkspace('B', h.selector);
  await first;
  assert.equal(second, false);
  assert.deepEqual(h.counts(), {paused:1,resumed:0,changed:0,loads:0});
  assert.equal(h.selector.disabled, false);
  assert.match(h.toasts.at(-1), /无法确认/);
});

function setupHarness({resumeReject=false, permissionReject=false}={}) {
  const listeners = {}, toasts = [], values = new Map([['ticket-mail-sound','1']]);
  const elements = new Map();
  const element = selector => elements.get(selector) || elements.set(selector, {classList:{toggle(){}}, textContent:'', checked:false, hidden:false, disabled:false}).get(selector);
  let resumes = 0;
  class AudioContext { constructor(){this.state='suspended'} async resume(){resumes++; if(resumeReject) throw new Error('blocked'); this.state='running'} }
  function Notification(){ throw new Error('constructor') }
  Notification.permission = 'default';
  Notification.requestPermission = async () => { if(permissionReject) throw new Error('permission'); return 'granted' };
  const document = {visibilityState:'visible', addEventListener:(name,fn)=>{listeners[name]=fn}, removeEventListener:(name,fn)=>{if(listeners[name]===fn)delete listeners[name]}};
  const storage = {getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value)};
  const controller = {start:async()=>{},stop(){},refresh(){},visibilityChanged(){},workspaceChanged(){},invalidateWorkspace(){}};
  const context = {state:{session:{user:{workspace_id:'A'}}},window:{AudioContext,Notification,addEventListener(){},focus(){}},Notification,document,localStorage:storage,
    MailRefresh:{createAlertPreferences:require('../static/mail-refresh.js').createAlertPreferences,createMailRefreshController:()=>controller},
    api:async()=>({}),load:async()=>true,openTicket(){},toast:message=>toasts.push(message),$:element,encodeURIComponent,setTimeout,clearTimeout};
  vm.createContext(context);
  vm.runInContext(extract('async function setupMailRefresh(', 'function renderNav('), context);
  return {context,listeners,toasts,values,elements,resumes:()=>resumes};
}

test('setupMailRefresh unlocks persisted sound on first real gesture without autoplay', async () => {
  const h = setupHarness();
  await h.context.setupMailRefresh();
  assert.equal(h.resumes(), 0);
  await h.listeners.pointerdown();
  await new Promise(setImmediate);
  assert.equal(h.resumes(), 1);
  assert.equal(h.values.get('ticket-mail-sound'), '1');
});

test('setupMailRefresh disables persisted sound with feedback when gesture resume rejects', async () => {
  const h = setupHarness({resumeReject:true});
  await h.context.setupMailRefresh();
  await h.listeners.keydown();
  await new Promise(setImmediate);
  assert.equal(h.elements.get('#mailSoundToggle').checked, false);
  assert.equal(h.values.get('ticket-mail-sound'), '0');
  assert.match(h.toasts.at(-1), /已关闭声音提醒/);
});

test('setupMailRefresh reports notification permission rejection without throwing', async () => {
  const h = setupHarness({permissionReject:true});
  await h.context.setupMailRefresh();
  await assert.doesNotReject(() => h.elements.get('#notificationPermission').onclick());
  assert.match(h.toasts.at(-1), /权限请求失败/);
});
