/* Optional Node + Python verification. DOM model and mocked network; does not assert browser pixel layout. */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { File } = require('node:buffer');
const project = require('node:path').resolve(__dirname, '..');
const trees = JSON.parse(require('node:child_process').execFileSync('python3', [require('node:path').join(__dirname, 'html-tree.py'), project], { encoding: 'utf8' }));
let checks = 0;
function ok(value, message) { assert.ok(value, message); checks++; }
class Elem {
  constructor(tag, attrs = {}, children = []) { this.tagName=tag.toUpperCase(); this.attrs={...attrs}; this.children=[]; this.listeners={}; this.value=''; this.disabled='disabled' in attrs; this.hidden='hidden' in attrs; this.open=false; this.files=[]; children.forEach(child=>this.append(child)); }
  get className(){ return this.attrs.class || ''; } set className(value){ this.attrs.class=value; }
  get classList(){ const self=this; return { contains(value){ return self.className.split(' ').includes(value); }, add(value){ if(!this.contains(value)) self.className+=' '+value; }, remove(value){self.className=self.className.split(' ').filter(x=>x!==value).join(' ');},toggle(value,force){let on=force??!this.contains(value);on?this.add(value):this.remove(value);return on;} }; }
  get dataset(){ const self=this; return new Proxy({}, {get:(_,key)=>self.attrs['data-'+key.replace(/[A-Z]/g,x=>'-'+x.toLowerCase())],set:(_,key,value)=>{self.attrs['data-'+key.replace(/[A-Z]/g,x=>'-'+x.toLowerCase())]=String(value);return true;}}); }
  setAttribute(key,value){ this.attrs[key]=String(value); } getAttribute(key){ return this.attrs[key]??null; } removeAttribute(key){delete this.attrs[key];}
  get type(){return this.attrs.type||'';} set type(value){this.attrs.type=value;}
  get textContent(){return this.tagName==='#TEXT'?this.value:this.children.map(child=>child.textContent).join('');}
  set textContent(value){if(this.tagName==='#TEXT'){this.value=String(value);return;} this.replaceChildren(new TextNode(String(value)));}
  append(...children){for(let child of children){if(typeof child==='string')child=new TextNode(child);if(child.parentNode)child.remove();child.parentNode=this;this.children.push(child);}}
  prepend(...children){for(let child of children.reverse()){if(typeof child==='string')child=new TextNode(child);if(child.parentNode)child.remove();child.parentNode=this;this.children.unshift(child);}}
  replaceChildren(...children){this.children.forEach(child=>child.parentNode=null);this.children=[];this.append(...children);}
  remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(child=>child!==this);this.parentNode=null;}
  matchesSimple(selector){if(selector.startsWith('#'))return this.attrs.id===selector.slice(1);if(selector.startsWith('.'))return this.classList.contains(selector.slice(1));let attr=selector.match(/^\[([^=\]]+)(?:="?([^"\]]+)"?)?\]$/);if(attr)return attr[1] in this.attrs&&(attr[2]===undefined||this.attrs[attr[1]]===attr[2]);return this.tagName.toLowerCase()===selector.toLowerCase();}
  matches(selector){let parts=selector.trim().split(/\s+/);if(!this.matchesSimple(parts.pop()))return false;let parent=this.parentNode;while(parts.length){let part=parts.pop();while(parent&&!parent.matchesSimple(part))parent=parent.parentNode;if(!parent)return false;parent=parent.parentNode;}return true;}
  querySelectorAll(selector){let choices=selector.split(',');let result=[];const walk=(parent)=>{for(let child of parent.children){if(child.tagName!=='#TEXT'){if(choices.some(s=>child.matches(s)))result.push(child);walk(child);}}};walk(this);return result;}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  addEventListener(type,handler){(this.listeners[type]??=[]).push(handler);}
  async emit(type,props={}){const event={target:this,preventDefault(){this.defaultPrevented=true;},...props};for(let handler of this.listeners[type]||[])await handler(event);return event;}
  closest(selector){let e=this;while(e){if(e.matches(selector))return e;e=e.parentNode;}return null;} click(){return this.emit('click');} focus(){this.ownerDocument.activeElement=this;} requestSubmit(){return this.emit('submit');}
  showModal(){this.open=true;} close(){this.open=false;this.emit('close');}
}
class TextNode extends Elem {constructor(value){super('#text');this.value=value;}}
function build(tree){if(typeof tree==='string')return new TextNode(tree);const e=new Elem(tree.tag,tree.attrs,tree.children.map(build));if(tree.tag==='option')e.value=tree.attrs.value??e.textContent;if(tree.tag==='select'){const options=e.querySelectorAll('option');e.value=(options.find(x=>'selected' in x.attrs)||options[0])?.value||'';}return e;}
function assignDocument(element,document){element.ownerDocument=document;element.children.forEach(child=>assignDocument(child,document));}
function environment(filename='index.html',width=1440,entries={},options={}) {
  const document=build(trees[filename]);document.body=document.querySelector('body');document.documentElement=document.querySelector('html');document.fonts={load:async()=>[{family:'SUIT Variable'}],check:()=>true};document.createElement=tag=>{let e=new Elem(tag);e.ownerDocument=document;return e;};document.createElementNS=(_,tag)=>document.createElement(tag);document.createTextNode=value=>{let e=new TextNode(value);e.ownerDocument=document;return e;};assignDocument(document,document);
  const memory=new Map(Object.entries(entries)), fetchCalls=[],responses=[],xhrCalls=[],xhrResponses=[],opened=[],timers=new Map();let timerId=0;
  class XHR {constructor(){this.upload={};this.headers={};}open(method,path){this.method=method;this.path=path;}setRequestHeader(key,value){this.headers[key]=value;}getResponseHeader(){return this.contentType||'application/json';}send(body){xhrCalls.push({method:this.method,path:this.path,headers:this.headers,body});queueMicrotask(()=>{const response=xhrResponses.shift()||{status:200,data:{success:true,document:{id:'a'.repeat(32),status:'stored'}}};if(response.defer){response.defer(this);return;}this.status=response.status;this.contentType=response.type;this.responseText=JSON.stringify(response.data);this.upload.onprogress?.({lengthComputable:true,loaded:1,total:2});this.upload.onprogress?.({lengthComputable:true,loaded:2,total:2});this.onload();});}}
  const listeners={};const location={pathname:'/'+filename,protocol:'https:',origin:'https://hs-rule.test',href:'https://hs-rule.test/'+filename,hash:'',search:''};
  const context={document,location,navigator:{platform:'MacIntel'},console,URL,URLSearchParams,FormData,File,Blob,Response,AbortController,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options?.detail;}},XMLHttpRequest:XHR,crypto:require('node:crypto').webcrypto,
    localStorage:{getItem:key=>memory.get(key)??null,setItem:(key,value)=>memory.set(key,String(value)),removeItem:key=>memory.delete(key)},
    matchMedia:query=>({matches:query.includes('reduced-motion')?!!options.reducedMotion:query.includes('760px')?width<=760:query.includes('coarse')?width<=760:false,addEventListener(){}}),
    setTimeout:callback=>{const id=++timerId;timers.set(id,callback);return id;},clearTimeout:id=>timers.delete(id),addEventListener(type,callback){(listeners[type]??=[]).push(callback);},dispatchEvent(event){for(const handler of listeners[event.type]||[])handler(event);return true;},open(url){const p={location:{href:url},opener:null,closed:false,close(){this.closed=true;}};opened.push(p);return p;},
    fetch:async(path,options={})=>{fetchCalls.push({path,...options});const response=responses.shift();if(response instanceof Error)throw response;if(typeof response==='function')return response();return response||new Response('Unavailable',{status:503});}
  };
  context.window=context;const scope=vm.createContext(context);vm.runInContext(fs.readFileSync(project+'/i18n.js','utf8'),scope);vm.runInContext(fs.readFileSync(project+'/api.js','utf8'),scope);
  return {context,scope,document,memory,fetchCalls,responses,xhrCalls,xhrResponses,opened,stepTimer(){const entry=timers.entries().next().value;if(!entry)return false;timers.delete(entry[0]);entry[1]();return true;},$:selector=>document.querySelector(selector),all:selector=>document.querySelectorAll(selector),boot(){vm.runInContext(fs.readFileSync(project+'/app.js','utf8'),scope);},async windowEvent(type){for(const callback of listeners[type]||[])await callback();}};
}
const json=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});
const pdf=(name='test.pdf',size=32,type='application/pdf')=>new File([Buffer.concat([Buffer.from('%PDF-1.7\n'),Buffer.alloc(Math.max(0,size-9))])],name,{type});
const pdfResponse=()=>new Response('%PDF-1.7\nverification',{headers:{'Content-Type':'application/pdf'}});
const idA='a'.repeat(32),idB='b'.repeat(32);
const metadata=(id=idA,filename='원본 규정.pdf',visibility='private')=>({id,title:'원본 제목',name:'원본 제목',filename,page_count:9,visibility,status:'stored',index_status:'not_connected',warnings:[],url:'/documents/'+id+'/file'});
const answer=()=>({answer:'실제 응답 검증용 원문\n<img src=x onerror=alert(1)>',sources:[{document_id:idA,page_start:2,page_end:4,title:'원본 제목',url:'/documents/'+idA+'/file',score:0.84}],found:true,followups:['원본 언어의 후속 질문']});
const flush=async()=>{for(let i=0;i<12;i++)await new Promise(resolve=>setImmediate(resolve));};
async function checkLanguageSwitch() {
  const view=environment();view.boot();
  const control=view.$('[data-language-toggle]');
  ok(control&&control.getAttribute('role')==='switch','language control is an accessible switch');
  ok(control.getAttribute('aria-checked')==='false','Korean starts with English mode off');
  await control.click();
  ok(view.document.documentElement.lang==='en'&&view.memory.get('hsu_language')==='en','one click changes and persists the interface language');
  ok(view.all('[data-language-toggle]').every(button=>button.getAttribute('aria-checked')==='true'),'header and mobile switches stay synchronized');
  await view.$('[data-od-id="menu-language-switch"]').click();
  ok(view.document.documentElement.lang==='ko'&&control.getAttribute('aria-checked')==='false','mobile switch changes the header state back');
}
async function checkAnimatedExamples() {
  const view=environment();view.boot();const input=view.$('#question-input'),frames=[];
  for(let i=0;i<200;i++){view.stepTimer();frames.push(input.placeholder);}
  ok(frames.some((value,index)=>index&&value.length>frames[index-1].length),'an idle example is typed into the placeholder');
  ok(frames.some((value,index)=>index&&value.length<frames[index-1].length),'the typed example is erased before cycling');
  await input.emit('focus');const focused=input.placeholder;
  for(let i=0;i<20;i++)view.stepTimer();
  ok(input.placeholder===focused,'placeholder animation pauses while the question field is focused');
  input.value='작성 중인 질문';await input.emit('input');await input.emit('blur');
  for(let i=0;i<20;i++)view.stepTimer();
  ok(input.value==='작성 중인 질문','example animation never changes the actual question');
  input.value='';await input.emit('input');await input.emit('blur');const resumed=input.placeholder;view.stepTimer();
  ok(input.placeholder!==resumed,'an empty question resumes its example after losing focus');
  input.value='규정 안내 질문';await input.emit('input');view.responses.push(json(answer()));
  await view.$('#question-form').emit('submit');await flush();const answered=input.placeholder;
  ok(input.value===''&&view.stepTimer()&&input.placeholder!==answered,'an unfocused empty question resumes examples after a successful answer');
  const reduced=environment('index.html',390,{}, {reducedMotion:true});reduced.boot();
  const still=reduced.$('#question-input').placeholder;for(let i=0;i<100;i++)reduced.stepTimer();
  ok(still.length>5&&reduced.$('#question-input').placeholder===still,'reduced motion uses a complete static example');
}
(async()=>{
  if(process.argv.includes('--animation-only')){await checkAnimatedExamples();console.log('Animated examples: PASS');return;}
  await checkLanguageSwitch();await checkAnimatedExamples();
  if(process.argv.includes('--feedback-only')){console.log('Language switch and animated examples: PASS');return;}
  const e=environment(),api=e.context.HSRuleAPI;
  e.responses.push(json(answer()));const history=Array.from({length:16},(_,i)=>({role:i%2?'assistant':'user',content:'prior '+i}));history.push({role:'system',content:'must be excluded'});await api.query('  질문  ',history,'en');let call=e.fetchCalls.at(-1),body=JSON.parse(call.body);ok(body.question==='질문'&&body.language==='en'&&body.messages.length===12,'query language and max 12 prior messages');ok(body.messages.every(m=>['user','assistant'].includes(m.role))&&!call.headers.Authorization,'history roles and public query');
  await assert.rejects(api.query('a'.repeat(2001)),err=>err.code==='question_too_long');checks++;await assert.rejects(api.query(' '),err=>err.code==='question_required');checks++;
  await assert.rejects(api.uploaded(),err=>err.status===401);checks++;api.saveToken('test-only-session');e.responses.push(json({rules:[]}));await api.uploaded();ok(e.fetchCalls.at(-1).headers.Authorization==='Bearer test-only-session','admin bearer compatibility');
  e.responses.push(json({detail:'external service',code:'rag_not_connected',request_id:'test-request'},503));await assert.rejects(api.query('test',[],'ko'),err=>err.code==='rag_not_connected'&&err.requestId==='test-request'&&err.status===503);checks++;
  await assert.rejects(api.removeUploaded('filename.pdf'),err=>err.code==='invalid_document_id');checks++;e.responses.push(new Response(null,{status:204}));await api.removeUploaded(idA);ok(e.fetchCalls.at(-1).path==='/uploaded-rules/'+idA,'delete by 32-character ID');
  await api.validatePDF(pdf('exact-limit.pdf',20*1024*1024));checks++;await assert.rejects(api.validatePDF(pdf('too-big.pdf',20*1024*1024+1)),err=>err.code==='file_too_large');checks++;await assert.rejects(api.validatePDF(pdf('wrong.docx')),err=>err.code==='invalid_pdf');checks++;await assert.rejects(api.validatePDF(new File(['not PDF'],'fake.pdf',{type:'application/pdf'})),err=>err.code==='invalid_pdf');checks++;
  const progress=[];e.xhrResponses.push({status:200,data:{success:true,document:metadata()}});await api.upload(pdf(),{onProgress:value=>progress.push(value)});assert.deepEqual(progress,[50,100]);checks++;call=e.xhrCalls.at(-1);ok(call.body.get('visibility')==='private'&&call.body.get('file').name==='test.pdf'&&!call.body.has('title'),'private default multipart');
  e.xhrResponses.push({status:200,data:{success:true,document:metadata()}});await api.upload(pdf(),{title:'수정 제목',visibility:'public'});ok(e.xhrCalls.at(-1).body.get('visibility')==='public'&&e.xhrCalls.at(-1).body.get('title')==='수정 제목','explicit title and public visibility');
  e.xhrResponses.push({status:409,data:{code:'duplicate_pdf',request_id:'duplicate-id'}});await assert.rejects(api.upload(pdf()),err=>err.code==='duplicate_pdf'&&err.requestId==='duplicate-id');checks++;
  e.xhrResponses.push({status:200,data:{success:true,document:{id:idA,status:'indexed'}}});await assert.rejects(api.upload(pdf()),err=>err.code==='invalid_response');checks++;
  e.responses.push(pdfResponse());await api.documentFile(idA,true);ok(e.fetchCalls.at(-1).path==='/documents/'+idA+'/file'&&e.fetchCalls.at(-1).headers.Authorization==='Bearer test-only-session','private original authenticated binary fetch');
  e.responses.push(new Response('<html>fallback</html>',{headers:{'Content-Type':'text/html'}}));await assert.rejects(api.rules(),err=>err.code==='invalid_response');checks++;
  e.responses.push(new Response('not a PDF',{headers:{'Content-Type':'application/pdf'}}));await assert.rejects(api.documentFile(idA,true),err=>err.code==='invalid_response');checks++;
  e.responses.push(pdfResponse());await api.exportAnswer('pdf',{title:'test',content:'actual content',question:'question',sources:answer().sources});checks++;
  await assert.rejects(api.exportAnswer('pdf',{content:'',sources:[]}),err=>err.code==='invalid_response');checks++;
  e.responses.push(json({summary:'original analysis',conflicts:[],recommendations:[]}));await api.conflict(pdf(),'en');ok(e.fetchCalls.at(-1).body.get('language')==='en'&&e.fetchCalls.at(-1).path==='/conflict/','analysis language multipart');
  ok(e.fetchCalls.every(call=>call.path.startsWith('/')&&!call.path.startsWith('//')),'all API calls same-origin');
  console.log('API contracts, PDF validation, storage semantics and protected originals: PASS');

  for(const lang of ['ko','en'])for(const width of [360,390,430,600,768,820,1024,1366,1440,1920]){
    const screen=environment('index.html',width,{hsu_language:lang});screen.boot();await flush();ok(screen.document.documentElement.lang===lang&&screen.$('#example-list').children.length===4,'language boot and examples at '+width);ok(screen.fetchCalls.length===0&&screen.$('#references').hidden,'honest default and no invented search');
    if(width<=800){await screen.$('[data-open-settings]').click();ok(screen.$('#settings-dialog').open,'mobile settings opens');ok(screen.$('#menu-audience')&&screen.$('[data-od-id="menu-language-switch"]'),'audience and language reachable on phone');await screen.$('[data-od-id="close-settings"]').click();ok(!screen.$('#settings-dialog').open,'mobile settings closes');}
  }
  const c=environment();c.boot();await c.$('.example-row').click();ok(c.$('#question-input').value.includes('휴학')&&c.fetchCalls.length===0,'question example inserts without sending');c.$('#header-audience').value='staff';await c.$('#header-audience').emit('change');ok(c.$('#example-list').textContent.includes('교직원')&&!c.memory.has('hsu_token'),'header audience personalizes examples only');
  await c.$('[data-od-id="language-switch"]').click();ok(c.$('[data-nav="chat"]').textContent==='Ask'&&c.document.documentElement.lang==='en','top-right language toggle');ok(c.$('#question-input').value.includes('휴학'),'language switch preserves original draft');ok(c.$('#example-list').textContent.includes('staff'),'English staff examples');
  await c.$('#new-question').click();c.$('#question-input').value='원본 언어 질문';let resolve; c.responses.push(()=>new Promise(r=>resolve=r));await c.$('#question-form').emit('submit');await flush();ok(c.$('#conversation').textContent.includes('Looking for supporting regulations'),'English query pending');resolve(json(answer()));await flush();ok(c.$('#conversation').textContent.includes('실제 응답 검증용 원문')&&!c.$('#references').hidden,'actual answer remains original language');ok(c.$('#references-content').textContent.includes('pp. 2–4'),'real PDF page range citations');ok(c.all('img').length===0&&c.$('#conversation').textContent.includes('<img src=x'),'server HTML safely stays text');ok(!c.$('#conversation').textContent.includes('84%'),'scores never shown');
  call=c.fetchCalls.at(-1);body=JSON.parse(call.body);ok(body.language==='en'&&body.messages.length===0&&body.question==='원본 언어 질문','query payload language and actual question');
  await c.$('[data-od-id="language-switch"]').click();ok(c.$('#references-content').textContent.includes('2–4쪽')&&c.$('#conversation').textContent.includes('실제 응답 검증용 원문'),'citations localize without translating answer');
  c.responses.push(pdfResponse());await c.$('[data-od-id="export-pdf-1"]').click();ok(c.fetchCalls.at(-1).path==='/export/pdf'&&JSON.parse(c.fetchCalls.at(-1).body).content.includes('실제 응답'),'export actual validated content');
  await c.$('.followup-button').click();ok(c.$('#question-input').value==='원본 언어의 후속 질문','followups preserve server language');c.responses.push(json({answer:'근거 없는 실제 반환문',found:false,sources:[],followups:[]}));await c.$('#question-form').emit('submit');await flush();ok(c.$('#conversation').textContent.includes('확인된 근거 없음'),'no evidence status');ok(JSON.parse(c.fetchCalls.at(-1).body).messages.length===2,'prior turns only');
  await c.$('#new-question').click();c.$('#question-input').value='연결 상태';c.responses.push(json({code:'rag_not_connected',detail:'private implementation detail',request_id:'req-1'},503));await c.$('#question-form').emit('submit');await flush();ok(c.$('#conversation').textContent.includes('아직 연결되지 않았습니다')&&!c.$('#conversation').textContent.includes('private implementation'),'RAG unavailable localized and internal detail hidden');
  await c.$('[data-od-id="language-switch"]').click();ok(c.$('#conversation').textContent.includes('answer service is not connected yet')&&c.$('#conversation').textContent.includes('Request ID: req-1'),'error and request ID translate live');
  c.responses.push(json({chapters:[{chapter:'원문 분류',rules:[metadata(idA,'규정 A.pdf','public'),metadata(idB,'규정 B.pdf','public')]}],total:999}));c.context.location.hash='#library';await c.windowEvent('hashchange');await flush();ok(!c.$('#library-view').hidden&&c.$('#library-count').textContent==='Showing 2 of 2','library usable after answer-service failure and actual counts');ok(c.$('#rules-list').textContent.includes('원본 제목')&&c.$('#rules-list').textContent.includes('Original stored'),'original titles and translated statuses');ok(c.$('#rules-list').textContent.includes('Answer search not connected'),'not_connected status accurate');
  c.$('#library-search').value='B.pdf';await c.$('#library-search').emit('input');ok(c.all('.document-table tbody tr').length===1,'filename search');c.$('#library-search').value='no match';await c.$('#library-search').emit('input');ok(c.$('#rules-list').textContent.includes('No documents match'),'localized compact filtered empty state');
  await c.$('#open-history').click();ok(c.$('#history-list').textContent.includes('원본 언어 질문'),'actual history restored');
  const persisted=Object.fromEntries(c.memory);const across=environment('login.html',390,persisted);across.boot();ok(across.document.documentElement.lang==='en'&&across.$('#menu-audience').value==='staff','language and audience persist across pages');
  for(const key of across.context.HSRuleI18n.keys)for(const language of ['ko','en']){across.context.HSRuleI18n.setLanguage(language);ok(across.context.HSRuleI18n.t(key)!==key,'translation pair exists: '+key);}
  console.log('Bilingual shell, draft preservation, page citations, history and library: PASS');

  const l=environment('login.html',390,{hsu_language:'en'});l.boot();await l.$('#login-form').emit('submit');ok(l.$('#login-error').textContent==='Enter your username.','English required username');l.$('#username').value='test-user';l.$('#password').value='test-only-password';await l.$('#toggle-password').click();ok(l.$('#password').type==='text'&&l.$('#toggle-password').getAttribute('aria-label')==='Hide password','password visibility accessible');
  l.responses.push(json({code:'admin_not_configured',request_id:'admin-req'},503));await l.$('#login-form').emit('submit');ok(l.$('#login-error').textContent.includes('not been configured'),'unconfigured admin distinct');await l.$('[data-od-id="language-switch"]').click();ok(l.$('#login-error').textContent.includes('아직 설정되지 않았습니다'),'login server error translates');
  l.responses.push(json({code:'invalid_credentials'},401));await l.$('#login-form').emit('submit');ok(l.$('#login-error').textContent.includes('아이디 또는 비밀번호'),'credentials rejection');l.context.location.search='?next=https://evil.test';l.responses.push(json({success:true,token:'test-only-session'}));await l.$('#login-form').emit('submit');ok(l.memory.get('hsu_token')==='test-only-session'&&l.$('#password').value===''&&l.context.location.href==='upload.html','token stored, password not stored and redirect allowlist');
  console.log('Focused sign-in, localized validation and session persistence: PASS');

  const locked=environment('upload.html',360,{hsu_language:'en'});locked.boot();ok(locked.fetchCalls.length===0&&locked.$('#upload-controls').disabled&&locked.$('#uploaded-list').textContent.includes('Sign in'),'locked admin state without unauthorized calls');
  const u=environment('upload.html',390,{hsu_token:'test-only-session',hsu_language:'en'});u.responses.push(json({rules:[metadata()]}));u.boot();await flush();ok(u.$('#auth-bar').hidden&&!u.$('#upload-controls').disabled&&u.$('#upload-visibility').value==='private','verified admin and private default');
  u.responses.push(pdfResponse());await u.$('.row-actions .button').click();ok(u.fetchCalls.at(-1).headers.Authorization==='Bearer test-only-session'&&u.opened.at(-1).location.href.startsWith('blob:'),'private original fetched with bearer then blob opened');
  u.$('#upload-files').files=[pdf('wrong.docx'),pdf('large.pdf',20*1024*1024+1),new File(['fake'],'invalid.pdf')];await u.$('#upload-files').emit('change');ok(u.all('.queue-row').length===0&&u.$('#upload-error').textContent.includes('valid PDF'),'invalid and oversized uploads blocked');
  u.$('#upload-files').files=[pdf('first.pdf'),pdf('second.pdf')];await u.$('#upload-files').emit('change');ok(u.all('.queue-row').length===2&&u.$('#upload-title').disabled,'multi-file queue and title omission');
  let finishUpload;u.xhrResponses.push({defer:xhr=>finishUpload=xhr},{status:409,data:{code:'duplicate_pdf',request_id:'dup-req'}});u.responses.push(json({rules:[metadata(),metadata(idB,'first.pdf')]}));const uploading=u.$('#start-upload').click();await flush();finishUpload.upload.onprogress({lengthComputable:true,loaded:10,total:10});ok(u.$('#upload-queue').textContent.includes('confirming storage')&&!u.$('#upload-queue').textContent.includes('Original stored'),'100% transfer does not claim storage');finishUpload.status=200;finishUpload.responseText=JSON.stringify({success:true,document:metadata(idB,'first.pdf')});finishUpload.onload();await uploading;ok(u.$('#upload-queue').textContent.includes('Original stored')&&u.$('#upload-queue').textContent.includes('already stored'),'stored status and duplicate error distinct');ok(u.xhrCalls.every(call=>call.body.get('visibility')==='private'),'batch private default');
  await u.$('[data-od-id="language-switch"]').click();ok(u.$('#upload-queue').textContent.includes('이미 저장된 PDF')&&u.$('#uploaded-list').textContent.includes('답변 검색 연결 전'),'dynamic upload/list statuses translate');
  const beforeDelete=u.fetchCalls.length;await u.$('[data-od-id="delete-'+idA+'"]').click();ok(u.$('#delete-dialog').open&&u.$('#delete-filename').textContent==='원본 규정.pdf'&&u.fetchCalls.length===beforeDelete,'human filename confirmation before ID deletion');await u.$('#cancel-delete').click();ok(u.fetchCalls.length===beforeDelete,'cancel does not delete');await u.$('[data-od-id="delete-'+idA+'"]').click();u.responses.push(new Response(null,{status:204}),json({rules:[]}));await u.$('#confirm-delete').click();ok(u.fetchCalls.some(call=>call.method==='DELETE'&&call.path==='/uploaded-rules/'+idA),'confirmed ID-only deletion');
  u.$('#conflict-file').files=[pdf('검토.pdf')];await u.$('#conflict-file').emit('change');let resolveConflict;u.responses.push(()=>new Promise(r=>resolveConflict=r));const reviewing=u.$('#start-conflict').click();await flush();ok(u.$('#conflict-results').textContent.includes('검토하고 있습니다'),'analysis pending localized');resolveConflict(json({code:'rag_not_connected',request_id:'analysis-req'},503));await reviewing;ok(u.$('#conflict-results').textContent.includes('문서 분석 서비스가 아직 연결되지 않았습니다'),'analysis not connected distinct from storage');await u.$('[data-od-id="language-switch"]').click();ok(u.$('#conflict-results').textContent.includes('analysis service is not connected yet'),'analysis failure English');
  u.responses.push(json({summary:'분석 원문',conflicts:[],recommendations:['원문 권고사항']}));await u.$('#start-conflict').click();ok(u.$('#conflict-results').textContent.includes('분석 원문')&&u.$('#conflict-results').textContent.includes('does not confirm'),'actual analysis stays original and no false all-clear');
  u.responses.push(json({code:'session_expired'},401));await u.$('#refresh-uploaded').click();ok(!u.memory.has('hsu_token')&&u.$('#upload-controls').disabled&&u.$('#auth-message').textContent.includes('session has expired'),'expired session locks admin functions');
  const forbidden=environment('upload.html',1440,{hsu_token:'test-only-session',hsu_language:'ko'});forbidden.responses.push(json({code:'forbidden'},403));forbidden.boot();await flush();ok(forbidden.$('#auth-message').textContent.includes('권한이 없습니다')&&forbidden.$('#upload-controls').disabled,'forbidden session explicit');
  const publicRead=environment('upload.html',1440,{hsu_token:'test-only-session'});publicRead.responses.push(json({rules:[metadata(idA,'public.pdf','public')]}));publicRead.boot();await flush();publicRead.responses.push(pdfResponse());await publicRead.$('.row-actions .button').click();ok(!publicRead.fetchCalls.at(-1).headers.Authorization,'known public original never depends on stored token');const privateExpired=environment('upload.html',390,{hsu_token:'test-only-session',hsu_language:'en'});privateExpired.responses.push(json({rules:[metadata()]}));privateExpired.boot();await flush();privateExpired.responses.push(json({code:'session_expired'},401));await privateExpired.$('.row-actions .button').click();ok(!privateExpired.memory.has('hsu_token')&&privateExpired.$('#upload-controls').disabled,'protected-original expiry immediately locks document manager');ok(privateExpired.$('#auth-message').textContent.includes('session has expired'),'protected-original expiry message persists');console.log('PDF-only uploads, progress, visibility, private originals, deletion and conflict review: PASS');
  console.log(`TOTAL: ${checks} assertions passed. DOM model covers both languages at all 10 required viewport widths; browser layout is checked separately.`);
})().catch(error=>{console.error(error);process.exitCode=1;});
