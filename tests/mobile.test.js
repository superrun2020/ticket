const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {File} = require('node:buffer');

// Run the production capture listener together with app.js's effective filter,
// load/render, refresh and workspace handlers. Only DOM and API I/O are fixtures.
function navigationHarness({mobile=true,kind='reply',workspaceResult='B'}={}) {
  const h=setup(mobile), {doc,media,addNode}=h;
  const q=s=>h.el(s)||addNode(s);
  const buttons=[];
  const button=(id,dataset={})=>{
    const node=q(id);node.dataset=dataset;
    node.closest=selector=>selector.split(',').some(part=>{
      part=part.trim();
      if(part==='.sidebar button:not(#mobileClose)')return id!=='#mobileClose';
      if(part.startsWith('.sidebar '))part=part.slice('.sidebar '.length);
      if(part==='button')return true;
      if(part===id)return true;
      const match=part.match(/^(?:button)?\[data-([\w-]+)\]$/);
      return match&&Object.hasOwn(dataset,match[1].replace(/-([a-z])/g,(_,c)=>c.toUpperCase()));
    })?node:null;
    buttons.push(node);return node;
  };
  const status=button('#pending',{status:'pending'});
  const mailbox=button('#mailbox',{mailbox:'M1'});
  const tag=button('#tag',{tag:'VIP'});
  const refresh=button('#refresh',{mailboxRefresh:''});
  const compose=button('#composeButton');
  const admin=button('#mailboxAdminNav');
  doc.querySelector=q;
  doc.querySelectorAll=selector=>buttons.filter(b=>b.closest(selector));
  const detail=q('#detail');
  const editor={value:'unsent draft'},form={editor};
  detail.children=[editor,form];
  detail.querySelector=selector=>{
    if(!detail.children.length)return null;
    return selector.split(',').some(s=>s.trim()===(kind==='reply'?'#replyText':'#composePageForm'))?(kind==='reply'?editor:form):null;
  };
  let observer=()=>{};
  Object.defineProperty(detail,'innerHTML',{set(value){this.children=[];this.html=value;observer()},get(){return this.html}});
  detail.classList.add('mobile-open');
  const file=new File(['draft'],'draft.txt',{type:'text/plain'});
  const attachments=[file],requests=[];
  const payload={tickets:[],mailboxes:[{id:'M1',enabled:true,name:'Inbox',email:'test@example.test',mailbox_tag:'VIP'}],category_options:[],counts:{},tag_options:['VIP'],category_counts:{}};
  const context={document:doc,window:{matchMedia:()=>media},URLSearchParams,
    MutationObserver:class {constructor(callback){observer=callback}observe(){}},
    state:{status:'all',mailbox:'all',tag:'all',q:'',view:'all',priority:'all',category:'all',sort:'latest',tickets:[],selected:'T1',session:{user:{workspace_id:'A'}}},
    replyComposerState:{ticketId:'T1'},attachmentState:{reply:kind==='reply'?attachments:[],compose:kind==='compose'?attachments:[]},
    workspaceGeneration:0,listRequestSequence:0,workspaceSessionDefinitive:true,pendingReplySends:[],refreshController:null,
    $:q,esc:String,initials:()=>'',toast(){},preloadMailService:async()=>{},
    api:async(url,options)=>{requests.push({url,options});if(url==='/api/workspaces/switch')return {};
      if(url==='/api/session')return {user:{workspace_id:workspaceResult},workspaces:[{id:'A',name:'A'},{id:'B',name:'B'}]};
      if(url.startsWith('/api/tickets?'))return payload;
      throw new Error(`Unexpected API request: ${url}`);
    }
  };
  vm.createContext(context);
  const app=fs.readFileSync(require.resolve('../static/app.js'),'utf8');
  const extract=(start,end)=>{assert.ok(app.includes(start)&&app.includes(end));return app.slice(app.indexOf(start),app.indexOf(end))};
  vm.runInContext(extract('function beginWorkspaceSwitch(', 'async function loadSession(')+
    extract('async function refreshSelectedMailbox(', 'function relativeTime(')+
    extract('function emptyHtml(', 'async function openTicket(')+
    extract("document.querySelectorAll('[data-status]')", "document.querySelectorAll('[data-view]')"),context);
  context.renderNav(payload);
  context.applySession({user:{workspace_id:'A'},workspaces:[{id:'A',name:'A'},{id:'B',name:'B'}]});
  vm.runInContext(fs.readFileSync(require.resolve('../static/mobile.js'),'utf8'),context);
  const shell={open:()=>q('#mobileMenu').listeners.click(),resume:()=>q('#mobileResume').listeners.click()};
  async function click(node){
    let prevented=false,stopped=false;
    // Click a nested span, as users do on rendered sidebar labels.
    const event={target:{closest:s=>node.closest(s)},preventDefault(){prevented=true},stopImmediatePropagation(){stopped=true}};
    doc.listeners.click(event);
    if(!stopped)await node.onclick?.(event);
    await new Promise(resolve=>setImmediate(resolve));
    return {prevented,stopped};
  }
  return {...h,q,context,click,shell,status,mailbox,tag,refresh,compose,admin,detail,editor,form,file,attachments,requests,
    async switchWorkspace(){const selector=q('#workspaceSwitcher');selector.value='B';const pending=selector.onchange({target:selector});selector.listeners.change();await pending;observer()}};
}

for(const kind of ['reply','compose'])for(const filter of ['status','mailbox','tag']) {
  test(`actual ${filter} navigation leaves ${kind} for list, preserves draft/File and Resume`,async()=>{
    const h=navigationHarness({kind});h.shell.open();
    assert.deepEqual(await h.click(h[filter]),{prevented:false,stopped:false});
    assert.equal(h.context.state[filter],{status:'pending',mailbox:'M1',tag:'VIP'}[filter]);
    assert.equal(h.requests.length,1,'existing handler must issue exactly one list request');
    assert.equal(new URL(h.requests[0].url,'https://example.test').searchParams.get(filter),h.context.state[filter]);
    assert.equal(h.detail.classList.contains('mobile-open'),false,'list must replace editor presentation');
    assert.equal(h.q('main').classList.contains('mobile-detail-visible'),false);
    assert.equal(h.q('#ticketList').inert,false);assert.equal(h.detail.inert,true);
    assert.equal(h.q('.sidebar').inert,true);assert.equal(h.q('main').inert,false);
    assert.equal(h.q('#mobileResume').hidden,false);
    h.q('#mobileResume').listeners.click();
    assert.equal(h.detail.inert,false);assert.equal(h.detail.children[0],h.editor);
    assert.equal(h.detail.children[1],h.form);assert.equal(h.editor.value,'unsent draft');
    assert.equal(h.context.attachmentState[kind],h.attachments);assert.equal(h.attachments[0],h.file);
    assert.equal(h.requests.length,1,'Resume must not load or replace the editor');
  });
}

test('actual mailbox refresh keeps editor open and request enabled; admin/compose are not list navigation',async()=>{
  const h=navigationHarness();h.context.state.mailbox='M1';h.shell.open();
  assert.deepEqual(await h.click(h.refresh),{prevented:false,stopped:false});
  assert.equal(h.requests.length,1);assert.equal(h.detail.classList.contains('mobile-open'),true);
  for(const node of [h.admin,h.compose]){
    let called=0;node.onclick=()=>{called++};h.shell.open();
    assert.deepEqual(await h.click(node),{prevented:false,stopped:false});
    assert.equal(called,1);assert.equal(h.detail.classList.contains('mobile-open'),true);
  }
});

test('desktop filter handler still loads without changing editor presentation',async()=>{
  const h=navigationHarness({mobile:false});
  assert.deepEqual(await h.click(h.status),{prevented:false,stopped:false});
  assert.equal(h.requests.length,1);assert.equal(h.detail.classList.contains('mobile-open'),true);
  assert.equal(h.q('#ticketList').inert,false);assert.equal(h.detail.inert,false);
});

for(const workspaceResult of ['A','B'])test(`actual workspace switch reconciled to ${workspaceResult} leaves usable list`,async()=>{
  const h=navigationHarness({workspaceResult});h.shell.open();await h.switchWorkspace();
  assert.equal(h.context.state.session.user.workspace_id,workspaceResult);
  assert.equal(h.q('.sidebar').inert,true);assert.equal(h.q('main').inert,false);
  assert.equal(h.q('#ticketList').inert,false);assert.equal(h.detail.classList.contains('mobile-open'),false);
  assert.equal(h.q('#workspaceSwitcher').disabled,false);
  assert.deepEqual(h.requests.slice(0,2).map(r=>r.url),['/api/workspaces/switch','/api/session']);
  if(workspaceResult==='A'){
    assert.equal(h.q('#mobileResume').hidden,false);h.shell.resume();
    assert.equal(h.detail.children[0],h.editor);assert.equal(h.attachments[0],h.file);
  }else{
    assert.equal(h.q('#mobileResume').hidden,true,'do not resume an old workspace editor');
    assert.equal(h.detail.children.length,0);assert.match(h.requests[2].url,/^\/api\/tickets\?/);
  }
});
function setup(mobile = true) {
  const nodes = new Map();
  const doc = {activeElement:null, listeners:{}, querySelector:s=>nodes.get(s), addEventListener(k,fn){this.listeners[k]=fn}};
  function el(s) {
    const classes=new Set(), attrs={};
    const node={hidden:false,inert:false,children:[],value:'draft',listeners:{},
      classList:{add:c=>classes.add(c),remove:c=>classes.delete(c),contains:c=>classes.has(c),toggle(c,on){on?classes.add(c):classes.delete(c)}},
      setAttribute:(k,v)=>attrs[k]=v,removeAttribute:k=>delete attrs[k],getAttribute:k=>attrs[k],
      focus(){doc.activeElement=node},addEventListener(k,fn){node.listeners[k]=fn},
      querySelector:()=>null,querySelectorAll:()=>node.children,getClientRects:()=>[{}],contains:n=>node.children.includes(n)};
    nodes.set(s,node);return node;
  }
  ['.sidebar','main','#mobileMenu','#mobileClose','#mobileBackdrop','#mobileBack','#mobileResume','#mobileBar','#detail','#ticketList'].forEach(el);
  const media={matches:mobile,addEventListener(k,fn){this.change=fn}};
  const context={module:{exports:{}},window:undefined};vm.createContext(context);
  const file=require('node:path').join(__dirname,'../static/mobile.js');
  vm.runInContext(fs.existsSync(file)?fs.readFileSync(file,'utf8'):'',context);
  assert.equal(typeof context.module.exports.createMobileShell,'function','mobile shell behavior must exist');
  const shell=context.module.exports.createMobileShell(doc,media);
  return {shell,doc,el:s=>nodes.get(s),media,addNode:el};
}
test('mobile same-editor selection resumes without dispatching destructive click; desktop does not intercept',()=>{
  const {shell,el,media}=setup();el('#detail').querySelector=()=>({value:'keep'});
  const card={dataset:{id:'A'}};let stopped=0;
  const event={target:{closest:s=>s==='.ticket-card'?card:null},preventDefault(){},stopImmediatePropagation(){stopped++}};
  assert.equal(typeof shell.select,'function','selection preservation must exist');
  shell.select(event,'A');assert.equal(stopped,1);assert.equal(el('#detail').classList.contains('mobile-open'),true);
  media.matches=false;shell.select(event,'A');assert.equal(stopped,1);
});
test('back and resume retain the exact editor and files; desktop resize releases drawer',()=>{
  const {shell,el,media}=setup(); const detail=el('#detail');
  const file={name:'draft.txt'},editor={value:'draft',files:[file]};detail.children=[editor];
  detail.querySelector=()=>editor; detail.classList.add('mobile-open');
  assert.equal(typeof shell.sync,'function','pane synchronization must exist');
  shell.sync();assert.equal(el('#ticketList').inert,true);
  shell.back();assert.equal(detail.classList.contains('mobile-open'),false);assert.equal(el('#ticketList').inert,false);
  assert.equal(el('#mobileResume').hidden,false);
  shell.resume();assert.equal(detail.classList.contains('mobile-open'),true);
  assert.equal(detail.children[0],editor);assert.equal(editor.files[0],file);
  shell.open();media.matches=false;media.change();
  assert.equal(el('.sidebar').inert,false);assert.equal(el('main').inert,false);assert.equal(detail.inert,false);
});
test('drawer exposes existing sidebar, traps focus, restores focus without changing draft nodes',()=>{
  const {shell,doc,el}=setup(); const detail=el('#detail');const draft={value:'unsent',files:[{name:'keep.txt'}]};detail.children=[draft];
  el('.sidebar').children=[el('#mobileClose'),el('#mobileMenu')];
  shell.open();assert.equal(el('.sidebar').inert,false);assert.equal(el('main').inert,true);
  assert.equal(el('#mobileMenu').getAttribute('aria-expanded'),'true');assert.equal(doc.activeElement,el('#mobileClose'));
  let prevented=false;shell.keydown({key:'Tab',shiftKey:true,preventDefault(){prevented=true}});
  assert.equal(prevented,true);assert.equal(doc.activeElement,el('#mobileMenu'));
  shell.keydown({key:'Escape',preventDefault(){}});
  assert.equal(el('main').inert,false);assert.equal(el('.sidebar').inert,true);assert.equal(doc.activeElement,el('#mobileMenu'));
  assert.equal(detail.children[0],draft);assert.equal(draft.files[0].name,'keep.txt');
});
