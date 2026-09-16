const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
function extract(start, end) { const a=source.indexOf(start), b=source.indexOf(end,a); if(a<0||b<0) throw Error('missing source'); return source.slice(a,b); }
function extractLast(start, end) { const a=source.lastIndexOf(start), b=source.indexOf(end,a); if(a<0||b<0) throw Error('missing source'); return source.slice(a,b); }

test('reply mode changes preserve body and selected files while applying server recipients', async () => {
  const elements = new Map();
  const el = key => elements.get(key) || elements.set(key, {value:'',checked:false,textContent:'',hidden:false}).get(key);
  el('#replyText').value='draft stays';
  const context={attachmentState:{reply:[{name:'draft.pdf'}]},replyComposerState:{parentMessageId:'parent',preview:{reply:{to:['alice@example.test'],cc:[]},reply_all:{to:['reply@example.test'],cc:['bob@example.test']}}},$:el};
  vm.createContext(context);
  vm.runInContext(extract('function applyReplyMode(', 'async function sendReply('), context);
  context.applyReplyMode('reply_all');
  assert.equal(el('#replyText').value,'draft stays');
  assert.equal(context.attachmentState.reply[0].name,'draft.pdf');
  assert.equal(el('#replyTo').value,'reply@example.test');
  assert.equal(el('#replyCc').value,'bob@example.test');
});

test('recipient refresh rejects A-B-A and same-editor stale responses', async () => {
  const pending=[];
  const context={state:{selected:'A',session:{user:{workspace_id:'one'}}},workspaceGeneration:0,replyEditorEpoch:1,workspaceSessionDefinitive:true,pendingReplySends:[],
    replyComposerState:{ticketId:'A',parentMessageId:'parent',mode:'reply'},api:()=>new Promise(resolve=>pending.push(resolve)),
    applyReplyMode(){context.applied=(context.applied||0)+1},toast(){}};
  vm.createContext(context);
  vm.runInContext(extract('async function refreshReplyRecipients(', 'function applyReplyMode('), context);
  const old=context.refreshReplyRecipients('A');
  context.replyEditorEpoch=2;
  const current=context.refreshReplyRecipients('A');
  pending[1]({parent_message_id:'new',reply:{to:['new@example.test'],cc:[]}});await current;
  pending[0]({parent_message_id:'old',reply:{to:['old@example.test'],cc:[]}});await old;
  assert.equal(context.replyComposerState.parentMessageId,'new');
  assert.equal(context.applied,1);

  const switched=context.refreshReplyRecipients('A');
  context.workspaceGeneration++;
  pending[2]({parent_message_id:'wrong'});await switched;
  assert.equal(context.replyComposerState.parentMessageId,'new');
});

test('openTicket rejects A-B-A, same-ticket reopen, and workspace-generation stale responses', async () => {
  const pending=[];
  const element=new Proxy({classList:{add(){},toggle(){}},focus(){},addEventListener(){},querySelector(){return {textContent:''}}},{get:(o,k)=>k in o?o[k]:null,set:(o,k,v)=>(o[k]=v,true)});
  const context={state:{selected:null,session:{user:{workspace_id:'one'}},categoryOptions:[],messageBodies:{}},workspaceGeneration:0,replyEditorEpoch:0,
    api:()=>new Promise(resolve=>pending.push(resolve)),renderList(){},$:()=>element,document:{querySelectorAll:()=>[]},categoryPickerMarkup:()=>'',messageHtml:m=>m.body,
    applyReplyMode(){},patchTicket(){},markNoReply(){},setupAttachmentPicker(){},sendReply(){},suggestReply(){},translateLatestIncoming(){},analyzeTicket(){},attachmentState:{reply:[]},toast(){},esc:s=>String(s)};
  vm.createContext(context);
  vm.runInContext(extract('openTicket=async function(id){', 'async function refreshReplyRecipients(')+';this.getComposer=()=>replyComposerState',context);
  const a1=context.openTicket('A');const b=context.openTicket('B');const a2=context.openTicket('A');
  pending[2]({ticket:{id:'A',subject:'new A'},messages:[],reply_recipients:{parent_message_id:'new'}});await a2;
  pending[0]({ticket:{id:'A',subject:'old A'},messages:[],reply_recipients:{parent_message_id:'old'}});await a1;
  pending[1]({ticket:{id:'B',subject:'B'},messages:[],reply_recipients:{parent_message_id:'b'}});await b;
  assert.equal(context.getComposer().parentMessageId,'new');
  const staleWorkspace=context.openTicket('A');context.workspaceGeneration++;
  pending[3]({ticket:{id:'A',subject:'wrong workspace'},messages:[],reply_recipients:{parent_message_id:'wrong'}});await staleWorkspace;
  assert.equal(context.getComposer().parentMessageId,'new');
});

test('sendReply empty To is blocked and stale completion preserves another editor', async () => {
  let resolveSend,opened=0,loaded=0,toastMessage='';
  class FormData { constructor(){this.values={}} append(k,v){this.values[k]=v} }
  const elements=new Map(),el=key=>elements.get(key)||elements.set(key,{value:'',checked:false,disabled:false}).get(key);
  el('#replyText').value='draft';el('#replyTo').value='';
  const context={state:{selected:'A',session:{user:{workspace_id:'one'}}},workspaceGeneration:0,replyEditorEpoch:1,workspaceSessionDefinitive:true,pendingReplySends:[],
    replyComposerState:{ticketId:'A',parentMessageId:null},attachmentState:{reply:[{name:'keep'}]},$:el,FormData,
    api:()=>new Promise(resolve=>{resolveSend=resolve}),toast:m=>{toastMessage=m},load:async()=>{loaded++},openTicket(){opened++}};
  vm.createContext(context);vm.runInContext(extract('function beginWorkspaceSwitch(', 'async function load(')+extractLast('async function sendReply(', 'async function markNoReply('),context);
  await context.sendReply('A');
  assert.match(toastMessage,/收件人/);assert.equal(resolveSend,undefined);
  el('#replyTo').value='one@example.test';const sending=context.sendReply('A');
  context.state.selected='B';context.replyEditorEpoch=2;context.workspaceGeneration++;
  resolveSend({ok:true});await sending;
  assert.equal(context.attachmentState.reply.length,1);assert.equal(loaded,0);assert.equal(opened,0);
});

test('successful send reconciles after failed workspace switch retains the same editor', async () => {
  let resolveSend,resolveSession,loaded=0,opened=0;
  const notices=[];
  class FormData { append(){} }
  const elements=new Map(),el=key=>elements.get(key)||elements.set(key,{value:'',checked:false,disabled:false}).get(key);
  el('#replyText').value='draft';el('#replyTo').value='one@example.test';
  const context={state:{selected:'A',session:{user:{workspace_id:'one'}},workspaceSwitchPending:false},workspaceGeneration:0,listRequestSequence:0,replyEditorEpoch:1,workspaceSessionDefinitive:true,pendingReplySends:[],
    replyComposerState:{ticketId:'A',parentMessageId:null},attachmentState:{reply:[{name:'sent.txt'}]},$:el,FormData,
    api:url=>url==='/api/workspaces/switch'?Promise.resolve({}):url==='/api/session'?new Promise(resolve=>{resolveSession=resolve}):new Promise(resolve=>{resolveSend=resolve}),
    toast:m=>notices.push(m),load:async()=>{loaded++;return true},openTicket:async()=>{opened++},refreshController:null};
  vm.createContext(context);
  vm.runInContext(extract('function beginWorkspaceSwitch(', 'async function load(')+extract('async function switchWorkspace(', 'async function loadSession(')+extractLast('async function sendReply(', 'async function markNoReply('),context);
  const sending=context.sendReply('A');
  const switching=context.switchWorkspace('two',el('#workspaceSwitcher'));
  await new Promise(resolve=>setImmediate(resolve));resolveSession({user:{workspace_id:'one'}});await switching;
  resolveSend({ok:true});await sending;
  assert.equal(el('#sendReply').disabled,true);
  assert.equal(context.attachmentState.reply.length,0);
  assert.equal(loaded,1);assert.equal(opened,1);
  assert.ok(notices.includes('邮件已进入发送队列'));
});

test('send finishing during unresolved switch waits for definitive retained session', async () => {
  let resolveSend,resolveSession,loaded=0;
  const notices=[];
  class FormData { append(){} }
  const elements=new Map(),el=key=>elements.get(key)||elements.set(key,{value:'',checked:false,disabled:false}).get(key);
  el('#replyText').value='draft';el('#replyTo').value='one@example.test';
  const context={state:{selected:'A',session:{user:{workspace_id:'one'}},workspaceSwitchPending:false},workspaceGeneration:0,listRequestSequence:0,replyEditorEpoch:1,workspaceSessionDefinitive:true,pendingReplySends:[],
    replyComposerState:{ticketId:'A',parentMessageId:null},attachmentState:{reply:[{name:'sent.txt'}]},$:el,FormData,
    api:url=>url==='/api/workspaces/switch'?Promise.resolve({}):url==='/api/session'?new Promise(resolve=>{resolveSession=resolve}):new Promise(resolve=>{resolveSend=resolve}),
    toast:m=>notices.push(m),load:async()=>{loaded++;return true},openTicket:async()=>{},refreshController:null};
  vm.createContext(context);
  vm.runInContext(extract('function beginWorkspaceSwitch(', 'async function load(')+extract('async function switchWorkspace(', 'async function loadSession(')+extractLast('async function sendReply(', 'async function markNoReply('),context);
  const sending=context.sendReply('A');
  const switching=context.switchWorkspace('two',el('#workspaceSwitcher'));
  resolveSend({ok:true});await sending;
  assert.equal(loaded,0);assert.equal(context.attachmentState.reply.length,1);
  assert.ok(notices.some(message=>message.includes('等待确认当前工作区')));
  resolveSession({user:{workspace_id:'one'}});await switching;
  assert.equal(loaded,1);assert.equal(context.attachmentState.reply.length,0);
  assert.ok(notices.includes('邮件已进入发送队列'));
});

test('failed send waits for definitive session and is suppressed after a different workspace wins', async () => {
  let rejectSend,resolveSession;
  const notices=[];
  class FormData { append(){} }
  const elements=new Map(),el=key=>elements.get(key)||elements.set(key,{value:'',checked:false,disabled:false,innerHTML:''}).get(key);
  el('#replyText').value='draft';el('#replyTo').value='one@example.test';
  const context={state:{selected:'A',session:{user:{workspace_id:'one'}},workspaceSwitchPending:false},workspaceGeneration:0,listRequestSequence:0,replyEditorEpoch:1,workspaceSessionDefinitive:true,pendingReplySends:[],
    replyComposerState:{ticketId:'A',parentMessageId:null},attachmentState:{reply:[{name:'keep.txt'}]},$:el,FormData,
    api:url=>url==='/api/workspaces/switch'?Promise.resolve({}):url==='/api/session'?new Promise(resolve=>{resolveSession=resolve}):new Promise((resolve,reject)=>{rejectSend=reject}),
    toast:m=>notices.push(m),load:async()=>{},openTicket:async()=>{},refreshController:null,emptyHtml:()=>'',applySession(session){context.state.session=session}};
  vm.createContext(context);
  vm.runInContext(extract('function beginWorkspaceSwitch(', 'async function load(')+extract('async function switchWorkspace(', 'async function loadSession(')+extractLast('async function sendReply(', 'async function markNoReply('),context);
  const sending=context.sendReply('A');
  const switching=context.switchWorkspace('two',el('#workspaceSwitcher'));
  rejectSend(new Error('INVALID_RECIPIENT'));await sending;
  assert.equal(el('#sendReply').disabled,true);
  assert.equal(context.pendingReplySends.length,1);
  assert.equal(notices.length,0);
  resolveSession({user:{workspace_id:'two'}});await switching;
  assert.equal(context.pendingReplySends.length,0);
  assert.equal(el('#sendReply').disabled,true);
  assert.equal(context.attachmentState.reply.length,1);
  assert.equal(notices.length,0);
});

test('failed send is reported only after unresolved switch confirms the same editor', async () => {
  let rejectSend,resolveSession;
  const notices=[];
  class FormData { append(){} }
  const elements=new Map(),el=key=>elements.get(key)||elements.set(key,{value:'',checked:false,disabled:false}).get(key);
  el('#replyText').value='draft';el('#replyTo').value='one@example.test';
  const context={state:{selected:'A',session:{user:{workspace_id:'one'}},workspaceSwitchPending:false},workspaceGeneration:0,listRequestSequence:0,replyEditorEpoch:1,workspaceSessionDefinitive:true,pendingReplySends:[],
    replyComposerState:{ticketId:'A',parentMessageId:null},attachmentState:{reply:[]},$:el,FormData,
    api:url=>url==='/api/workspaces/switch'?Promise.resolve({}):url==='/api/session'?new Promise(resolve=>{resolveSession=resolve}):new Promise((resolve,reject)=>{rejectSend=reject}),
    toast:m=>notices.push(m),load:async()=>{},openTicket:async()=>{},refreshController:null};
  vm.createContext(context);
  vm.runInContext(extract('function beginWorkspaceSwitch(', 'async function load(')+extract('async function switchWorkspace(', 'async function loadSession(')+extractLast('async function sendReply(', 'async function markNoReply('),context);
  const sending=context.sendReply('A');const switching=context.switchWorkspace('two',el('#workspaceSwitcher'));
  rejectSend(new Error('INVALID_RECIPIENT'));await sending;
  assert.equal(el('#sendReply').disabled,true);assert.equal(notices.length,0);
  resolveSession({user:{workspace_id:'one'}});await switching;
  assert.equal(el('#sendReply').disabled,false);
  assert.ok(notices.includes('收件人格式不正确'));
});

test('queued send survives refresh failure with cleared draft and safe retry state', async () => {
  let resolveSend;
  const notices=[];
  class FormData { append(){} }
  const elements=new Map(),el=key=>elements.get(key)||elements.set(key,{value:'',checked:false,disabled:false}).get(key);
  el('#replyText').value='submitted draft';el('#replyTo').value='one@example.test';
  const context={state:{selected:'A',session:{user:{workspace_id:'one'}}},workspaceGeneration:0,replyEditorEpoch:1,workspaceSessionDefinitive:true,pendingReplySends:[],
    replyComposerState:{ticketId:'A',parentMessageId:null},attachmentState:{reply:[{name:'sent.txt'}]},$:el,FormData,
    api:()=>new Promise(resolve=>{resolveSend=resolve}),toast:m=>notices.push(m),load:async()=>{throw new Error('NETWORK')},openTicket:async()=>{throw new Error('should not open')}};
  vm.createContext(context);
  vm.runInContext(extract('function beginWorkspaceSwitch(', 'async function load(')+extractLast('async function sendReply(', 'async function markNoReply('),context);
  const sending=context.sendReply('A');resolveSend({ok:true});await sending;
  assert.equal(context.pendingReplySends.length,0);
  assert.equal(el('#replyText').value,'');assert.equal(context.attachmentState.reply.length,0);
  assert.equal(el('#sendReply').disabled,false);
  assert.ok(notices.includes('邮件已进入发送队列，但工单刷新失败，请手动刷新'));
  assert.ok(!notices.some(message=>message.startsWith('发送失败')));
});

test('deferred queued send refresh failure does not reject workspace reconciliation', async () => {
  let resolveSend,resolveSession;
  const notices=[];
  class FormData { append(){} }
  const elements=new Map(),el=key=>elements.get(key)||elements.set(key,{value:'',checked:false,disabled:false}).get(key);
  el('#replyText').value='submitted draft';el('#replyTo').value='one@example.test';
  const context={state:{selected:'A',session:{user:{workspace_id:'one'}},workspaceSwitchPending:false},workspaceGeneration:0,listRequestSequence:0,replyEditorEpoch:1,workspaceSessionDefinitive:true,pendingReplySends:[],
    replyComposerState:{ticketId:'A',parentMessageId:null},attachmentState:{reply:[{name:'sent.txt'}]},$:el,FormData,
    api:url=>url==='/api/workspaces/switch'?Promise.resolve({}):url==='/api/session'?new Promise(resolve=>{resolveSession=resolve}):new Promise(resolve=>{resolveSend=resolve}),
    toast:m=>notices.push(m),load:async()=>{throw new Error('NETWORK')},openTicket:async()=>{},refreshController:null};
  vm.createContext(context);
  vm.runInContext(extract('function beginWorkspaceSwitch(', 'async function load(')+extract('async function switchWorkspace(', 'async function loadSession(')+extractLast('async function sendReply(', 'async function markNoReply('),context);
  const sending=context.sendReply('A');const switching=context.switchWorkspace('two',el('#workspaceSwitcher'));
  resolveSend({ok:true});await sending;
  assert.equal(context.pendingReplySends.length,1);
  resolveSession({user:{workspace_id:'one'}});
  assert.equal(await switching,false);
  assert.equal(context.pendingReplySends.length,0);
  assert.equal(el('#replyText').value,'');assert.equal(context.attachmentState.reply.length,0);
  assert.equal(el('#sendReply').disabled,false);
  assert.ok(notices.includes('邮件已进入发送队列，但工单刷新失败，请手动刷新'));
});

test('refresh failure waits for a workspace switch to become definitive before resolving retained editor', async () => {
  let resolveSend,rejectLoad;
  const notices=[];
  class FormData { append(){} }
  const elements=new Map(),el=key=>elements.get(key)||elements.set(key,{value:'',checked:false,disabled:false}).get(key);
  el('#replyText').value='submitted draft';el('#replyTo').value='one@example.test';
  const context={state:{selected:'A',session:{user:{workspace_id:'one'}}},workspaceGeneration:0,listRequestSequence:0,replyEditorEpoch:1,workspaceSessionDefinitive:true,pendingReplySends:[],
    replyComposerState:{ticketId:'A',parentMessageId:null},attachmentState:{reply:[{name:'sent.txt'}]},$:el,FormData,
    api:()=>new Promise(resolve=>{resolveSend=resolve}),toast:m=>notices.push(m),load:()=>new Promise((resolve,reject)=>{rejectLoad=reject}),openTicket:async()=>{},refreshController:null};
  vm.createContext(context);
  vm.runInContext(extract('function beginWorkspaceSwitch(', 'async function load(')+extractLast('async function sendReply(', 'async function markNoReply('),context);
  const sending=context.sendReply('A');resolveSend({ok:true});await new Promise(resolve=>setImmediate(resolve));
  context.beginWorkspaceSwitch();rejectLoad(new Error('NETWORK'));await sending;
  assert.equal(context.workspaceSessionDefinitive,false);assert.equal(context.pendingReplySends.length,1);
  assert.equal(el('#sendReply').disabled,true);
  assert.ok(!notices.some(message=>message.includes('工单刷新失败')));
  context.workspaceSessionDefinitive=true;await context.reconcilePendingReplySends();
  assert.equal(context.pendingReplySends.length,0);assert.equal(el('#sendReply').disabled,false);
  assert.equal(el('#replyText').value,'');assert.equal(context.attachmentState.reply.length,0);
  assert.equal(notices.filter(message=>message==='邮件已进入发送队列').length,1);
  assert.ok(notices.includes('邮件已进入发送队列，但工单刷新失败，请手动刷新'));
});

test('refresh failure from a replaced same-ticket editor is discarded without touching its draft', async () => {
  let resolveSend,rejectLoad;
  const notices=[];
  class FormData { append(){} }
  const elements=new Map(),el=key=>elements.get(key)||elements.set(key,{value:'',checked:false,disabled:false}).get(key);
  el('#replyText').value='submitted draft';el('#replyTo').value='one@example.test';
  const context={state:{selected:'A',session:{user:{workspace_id:'one'}}},workspaceGeneration:0,listRequestSequence:0,replyEditorEpoch:1,workspaceSessionDefinitive:true,pendingReplySends:[],
    replyComposerState:{ticketId:'A',parentMessageId:null},attachmentState:{reply:[{name:'sent.txt'}]},$:el,FormData,
    api:()=>new Promise(resolve=>{resolveSend=resolve}),toast:m=>notices.push(m),load:()=>new Promise((resolve,reject)=>{rejectLoad=reject}),openTicket:async()=>{}};
  vm.createContext(context);
  vm.runInContext(extract('function beginWorkspaceSwitch(', 'async function load(')+extractLast('async function sendReply(', 'async function markNoReply('),context);
  const sending=context.sendReply('A');resolveSend({ok:true});await new Promise(resolve=>setImmediate(resolve));
  context.replyEditorEpoch++;el('#replyText').value='new draft';el('#sendReply').disabled=true;
  rejectLoad(new Error('NETWORK'));await sending;
  assert.equal(el('#replyText').value,'new draft');assert.equal(el('#sendReply').disabled,true);
  assert.ok(!notices.some(message=>message.includes('工单刷新失败')));
  assert.equal(context.pendingReplySends.length,0);
});

test('outbound messageHtml escapes saved To and Cc and never renders Bcc', () => {
  const context={state:{messageBodies:{}},esc:s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;'),initials:()=>'',deliveryStatusMarkup:()=>'',attachmentLinks:()=>'',humanSize:()=>'',Date};
  vm.createContext(context);vm.runInContext(extractLast('function messageHtml(m){', 'openTicket=async function'),context);
  const html=context.messageHtml({id:'m',direction:'outbound',sender_name:'Agent',created_at:'2024-01-01',body:'ok',to:['safe@example.test','<img>'],cc:['cc@example.test'],bcc:['secret@example.test'],attachments:[]});
  assert.match(html,/safe@example\.test/);assert.match(html,/&lt;img&gt;/);assert.match(html,/cc@example\.test/);assert.doesNotMatch(html,/secret@example\.test|Bcc/);
});
