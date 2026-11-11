const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const original=Module._load;Module._load=function(name,...args){return name==='canvas'?{}:original.call(this,name,...args)};const {JSDOM}=Module.createRequire(require.resolve('jest-environment-jsdom',{paths:[process.env.AGENT_WORK_JSDOM_ROOT||process.cwd()]}))('jsdom');Module._load=original;
const asset=n=>fs.readFileSync(path.join(__dirname,'../agent_work/assets',n),'utf8'),tick=()=>new Promise(setImmediate);
function setup(t,enabled=true,fail=false){const dom=new JSDOM(asset('workforce.html'),{url:'http://localhost/team?project=p',runScripts:'outside-only'});t.after(()=>dom.window.close());const w=dom.window,calls=[];w.HTMLCanvasElement.prototype.getContext=()=>null;w.eval(asset('office-scene.js'));w.eval(asset('office.js'));const actions=Object.fromEntries(['hire','edit','chat','job','goal','request'].map(key=>[key,async(...args)=>{calls.push([key,...args]);if(fail)throw Error('save failed');}]));const ui=w.TackOffice.mount(actions);ui.update({execution_enabled:enabled,settings:{roles:{dev:{name:'개발'}},runtime:'codex-app-server'},goals:[{id:'g',mode:'team',status:'awaiting_acceptance',title:'최종 검증'}],office:{employees:[{id:'dev',name:'개발',state:'queued',instructions:'<img src=x onerror=bad()>',current_job:{goal_id:'g',title:'계산기'},done_jobs:1}],jobs:[{id:'j',goal_id:'g',employee:'dev',status:'waiting_user',title:'조건 확인',goal_title:'계산기'}],reports:[{id:'j',goal_id:'g',employee:'dev',title:'검증',result_preview:'<script>bad()</script>'}],counts:{working:0,waiting:2,done:1}}});return {w,ui,calls,$:id=>w.document.getElementById(id)};}
test('office views share authoritative jobs and escape text',t=>{const {w,ui,$}=setup(t);assert.equal(w.document.querySelectorAll('.studio-desk').length,1);assert.equal(w.document.querySelector('img'),null);assert.equal($('office-person'),null);ui.select('dev');assert.match($('office-person').textContent,/Codex app-server/);assert.equal(w.document.querySelector('.studio-desk').getAttribute('aria-pressed'),'true');ui.setView('board');assert.match($('office-body').textContent,/조건 확인/);ui.setView('inbox');assert.match($('office-body').textContent,/최종 검증/);ui.setView('reports');assert.match($('office-body').textContent,/<script>/);assert.equal(w.document.querySelector('script:not([src])'),null);ui.setView('chat');assert.equal(w.document.querySelector('.team-room').hidden,false);assert.equal($('office-main').hidden,true);});
test('named employee request dispatches exact target and preserves drafts across views',async t=>{const {w,ui,calls,$}=setup(t);$('office-request-text').value='@개발 계산기 구현';ui.setView('board');ui.setView('office');assert.equal($('office-request-text').value,'@개발 계산기 구현');$('office-request').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();assert.deepEqual(calls,[['request','계산기 구현','dev']]);assert.equal($('office-request-text').value,'');assert.equal($('office-main').hidden,true);});
test('failed request and unknown mentions never lose a draft',async t=>{const {w,calls,$}=setup(t,true,true);$('office-request-text').value='@unknown 구현';$('office-request').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();assert.equal(calls.length,0);$('office-request-text').value='구현';$('office-request').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();assert.equal($('office-request-text').value,'구현');assert.match($('office-request-error').textContent,/save failed/);});
test('readonly office disables hiring editing and dispatch while keeping records readable',async t=>{const {w,ui,calls,$}=setup(t,false);ui.select('dev');for(const selector of ['.studio-hire','.person-actions>.button:last-child'])assert.equal(w.document.querySelector(selector).disabled,true);assert.equal($('office-request-send').disabled,true);$('office-request-text').value='구현';$('office-request').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();assert.equal(calls.length,0);});

test('project selector stays unique and moves with office and conversation views',t=>{const {w,ui,$}=setup(t);const picker=$('project-picker');assert.ok(picker.closest('.office-project-slot'));assert.equal(w.document.body.dataset.officeView,'office');ui.setView('chat');assert.ok(picker.closest('.missions'));ui.setView('office');assert.equal($('project-picker'),picker);assert.equal(w.document.querySelectorAll('#project-picker').length,1);});

test('scene selection opens details and closing returns keyboard focus to the same employee',async t=>{const {w,$}=setup(t);w.document.querySelector('.studio-desk').click();await tick();assert.ok($('office-person'));w.document.querySelector('.person-close').click();await tick();assert.equal($('office-person'),null);assert.equal(w.document.activeElement.dataset.employee,'dev');});
test('isometric depth order puts a chair seat behind its seated employee',t=>{const {w}=setup(t),scene=w.TackOfficeScene;assert.deepEqual(JSON.parse(JSON.stringify(scene.project(2,1))),{x:32,y:48});const employee={id:'employee',box:{x:1,y:1,w:.36,d:.36,z:12,h:30}},seat={id:'seat',box:{x:1,y:1,w:1,d:1,z:0,h:12}};assert.equal(scene.depthOrder([employee,seat])[0].id,'seat');});

test('employees remain selectable after dragging the office camera',async t=>{const {w,$}=setup(t);const canvas=w.document.querySelector('.office-canvas');canvas.dispatchEvent(new w.MouseEvent('pointerdown',{button:0,clientX:20,clientY:20}));canvas.dispatchEvent(new w.MouseEvent('pointermove',{clientX:90,clientY:40}));canvas.dispatchEvent(new w.MouseEvent('pointerup'));w.document.querySelector('.studio-desk').click();await tick();assert.ok($('office-person'));});

test('motion pause control survives employee selection and remains keyboard accessible',async t=>{
  const {w,ui}=setup(t);
  let control=w.document.querySelector('[aria-label="사무실 애니메이션 일시정지"]');
  assert.ok(control.querySelector('svg'));
  control.click();await tick();
  assert.equal(control.getAttribute('aria-pressed'),'true');
  ui.select('dev');
  control=w.document.querySelector('[aria-label="사무실 애니메이션 재생"]');
  assert.equal(control.getAttribute('aria-pressed'),'true');
  control.focus();assert.equal(w.document.activeElement,control);
  control.click();await tick();
  assert.equal(control.getAttribute('aria-pressed'),'false');
});

test('long staff guidance is expandable after action buttons and remains fully readable',t=>{
  const {w,ui}=setup(t);
  const text='책임과 검증 조건을 보존합니다. '.repeat(70);
  ui.update({execution_enabled:true,settings:{roles:{dev:{name:'개발'}}},goals:[],office:{employees:[{id:'dev',name:'개발',state:'idle',instructions:text}],jobs:[],reports:[],counts:{}}});
  ui.select('dev');
  const panel=w.document.getElementById('office-person'),details=panel.querySelector('.person-guidance');
  assert.equal(details.open,false);
  assert.equal(details.querySelector('p').textContent,text);
  assert.ok(panel.querySelector('.person-responsibility-preview').textContent.length<=181);
  assert.ok(panel.querySelector('.person-actions').compareDocumentPosition(details)&w.Node.DOCUMENT_POSITION_FOLLOWING);
});

test('mobile closing the staff detail restores focus to its visible staff picker',async t=>{
  const {w,ui}=setup(t);w.matchMedia=()=>({matches:true,addEventListener(){},removeEventListener(){}});
  ui.select('dev');w.document.querySelector('.person-close').click();await tick();
  assert.equal(w.document.activeElement.getAttribute('aria-label'),'사무실 직원 선택');
});
