const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const originalLoad = Module._load;
Module._load = function (name, ...args) { return name === 'canvas' ? {} : originalLoad.call(this, name, ...args); };
const { JSDOM } = Module.createRequire(require.resolve('jest-environment-jsdom', { paths: [process.env.AGENT_WORK_JSDOM_ROOT || process.cwd()] }))('jsdom');
Module._load = originalLoad;
const asset = n => fs.readFileSync(path.join(__dirname, '../agent_work/assets', n), 'utf8');
const tick = async () => { await new Promise(setImmediate); await new Promise(setImmediate); };
const policy = () => ({ version: 2, autopilot: false, max_rounds: 2, max_minutes: 30, max_tokens: 500000, roles: Object.fromEntries([['plan','기획'],['implement','구현'],['verify','검토']].map(([r,name]) => [r,{name,model:'',instructions:''}])) });
const goal = (id='GOAL-A') => ({ id, project_id:'owner', project_name:'demo', title:'<script>bad()</script>', status:'ready', version:9, acceptance:'test passes', next_step:0, steps:[{title:'Fix source',task_id:'LOCAL-0001',role:'implement',prompt:'Edit source.py',files:['source.py'],task_status:'todo'}],verification:[['python3','-m','unittest']],worker:'' });
function setup(t, {goals=[goal()], enabled=true, mutate}={}) {
  const dom = new JSDOM(asset('goals.html'),{url:'http://127.0.0.1:8789/goals?all=1&project=selected',runScripts:'outside-only'});t.after(()=>dom.window.close());const w=dom.window;const calls=[];
  w.crypto.randomUUID = require('node:crypto').randomUUID;
  w.workUrl = p => { const u=new URL(p,w.location.origin);u.searchParams.set('project','selected');return u.pathname+u.search; };
  w.fetch = async (url,options={}) => {
    calls.push([url,options]);
    if(options.method) return mutate ? mutate(url,JSON.parse(options.body),options) : {ok:true,json:async()=>({})};
    if(url.includes('/api/context')) { const id=new URL(url,w.location.origin).searchParams.get('project');return {ok:true,json:async()=>({project:{id,name:id},projects:[{id,name:id,orchestration_enabled:true}]})}; }
    if(url.includes('/api/team/catalog')) return {ok:true,json:async()=>({source:{commit:'2823b93a39bc89f041aa8cfc27a11c2249edc1de'},roles:[{id:'frontend_web',label:'프론트엔드 개발자(웹)',config:{name:'프론트엔드웹',kind:'implement',model:'',instructions:'Crewset 웹 책임',skills:[]}}]})};
    if(url.includes('/api/team/settings')) return {ok:true,json:async()=>policy()};
    if(url.includes('/chat')) {const id=url.split('/')[3];return {ok:true,json:async()=>({goal:goals.find(g=>g.id===id),settings:policy(),execution_enabled:enabled,runs:[],messages:[{id:'m1',sender:'user',kind:'goal',body:'<img src=x onerror=bad()>',created_at:'2026-10-28T01:00:00Z',data:{acceptance:'test passes'}}]})};}
    return {ok:true,json:async()=>({goals,project:{id:'selected',name:'selected project'},execution_enabled:enabled})};
  };
  w.eval(asset('goals.js'));return {w,calls,$:id=>w.document.getElementById(id)};
}
const submit = (w,form) => form.dispatchEvent(new w.Event('submit',{cancelable:true}));
test('chat escapes real content and details and targets owning project on approval',async t=>{
  const {w,calls,$}=setup(t);await tick();
  assert.equal($('chat-feed').querySelector('img'),null);assert.equal($('room-title').querySelector('script'),null);
  assert.match($('chat-feed').textContent,/<img/);assert.match($('chat-feed').textContent,/test passes/);
  assert.match($('scope-description').textContent,/전체 프로젝트/);
  $('run-action').click();await tick();const [url,o]=calls.find(([,o])=>o.method==='POST');
  assert.equal(url,'/api/goals/GOAL-A/start?project=owner');assert.deepEqual(JSON.parse(o.body),{version:9});
});
test('readonly empty dashboard disables creation and does not show composer',async t=>{
  const {$}=setup(t,{goals:[],enabled:false});await tick();assert.equal($('new-goal').disabled,true);assert.equal($('message-form').hidden,true);assert.match($('connection-status').textContent,/조회 모드/);
});
test('natural-language goal preserves draft and names creation project on failure',async t=>{
  const {w,calls,$}=setup(t,{mutate:async()=>({ok:false,json:async()=>({error:'검증 명령 확인'})})});await tick();$('new-goal').click();
  const form=$('goal-form');form.elements.title.value='로그인 오류 수정';form.elements.acceptance.value='안내';form.elements.verification_text.value='pnpm test';submit(w,form);await tick();
  const [url,o]=calls.find(([,o])=>o.method);assert.equal(url,'/api/goals/draft?project=owner');assert.equal(JSON.parse(o.body).verification_text,'pnpm test');assert.equal(form.elements.title.value,'로그인 오류 수정');assert.match($('form-error').textContent,/검증 명령/);assert.match($('goal-project-hint').textContent,/demo/);
});
test('notes save without execution purpose and failed retries retain draft and idempotency key',async t=>{
  const {w,calls,$}=setup(t,{mutate:async()=>({ok:false,json:async()=>({error:'상태 변경됨'})})});await tick();$('message-body').value='결정사항';submit(w,$('message-form'));await tick();submit(w,$('message-form'));await tick();
  const posts=calls.filter(([,o])=>o.method);assert.equal(posts.length,2);const a=JSON.parse(posts[0][1].body),b=JSON.parse(posts[1][1].body);assert.equal(a.kind,'note');assert.equal(a.client_id,b.client_id);assert.equal($('message-body').value,'결정사항');assert.match($('goal-error').textContent,/변경됨/);assert.equal(posts[0][0],'/api/goals/GOAL-A/messages?project=owner');
});
test('mention questions route explicit roles and mismatches stay local',async t=>{
  const {w,calls,$}=setup(t);await tick();$('message-kind').value='question';$('message-kind').dispatchEvent(new w.Event('change'));$('message-body').value='@구현 설명해줘';submit(w,$('message-form'));await tick();
  const body=JSON.parse(calls.find(([,o])=>o.method)[1].body);assert.equal(body.target,'implement');assert.equal(body.kind,'question');assert.equal($('message-body').value,'');
  $('message-target').value='verify';$('message-body').value='@구현 설명해줘';submit(w,$('message-form'));await tick();assert.equal(calls.filter(([,o])=>o.method).length,1);assert.match($('goal-error').textContent,/다릅니다/);
});
test('settings use PATCH with version and retain fields on conflict',async t=>{
  const {w,calls,$}=setup(t,{mutate:async()=>({ok:false,json:async()=>({error:'다른 곳에서 설정 변경'})})});await tick();$('open-settings').click();await tick();const form=$('settings-form');form.elements.autopilot.checked=true;form.elements.implement_instructions.value='Keep scoped';submit(w,form);await tick();
  const [url,o]=calls.find(([,o])=>o.method);const body=JSON.parse(o.body);assert.equal(url,'/api/team/settings?project=owner');assert.equal(o.method,'PATCH');assert.equal(body.version,2);assert.equal(body.autopilot,true);assert.equal(body.roles.implement.instructions,'Keep scoped');assert.equal(form.elements.implement_instructions.value,'Keep scoped');assert.match($('settings-error').textContent,/설정 변경/);
});
test('running goal keeps notes enabled while preventing parallel role execution',async t=>{
  const g=goal();g.worker='worker';g.status='running';g.execution_settings={...policy(),autopilot:true};const {w,$}=setup(t,{goals:[g]});await tick();assert.equal($('send-message').disabled,false);assert.match($('room-mode').textContent,/Autopilot.*실행 중/);$('message-kind').value='review';$('message-kind').dispatchEvent(new w.Event('change'));assert.equal($('send-message').disabled,true);assert.equal($('message-target').value,'verify');assert.equal($('run-action').textContent,'실행 중단');
});
test('changing channels keeps independent unsent drafts',async t=>{
  const {w,$}=setup(t,{goals:[goal(),goal('GOAL-B')]});await tick();$('message-body').value='A draft';$('channels').querySelector('[data-goal="GOAL-B"]').click();await tick();assert.equal($('message-body').value,'');$('message-body').value='B draft';$('channels').querySelector('[data-goal="GOAL-A"]').click();await tick();assert.equal($('message-body').value,'A draft');
});
test('project team can add an evaluator with explicit skill and preserve existing roles',async t=>{
  const {w,calls,$}=setup(t);await tick();$('open-settings').click();await tick();$('add-role').click();
  const field=$('role-fields').lastElementChild;const key=field.dataset.role;const form=$('settings-form');form.elements[`${key}_id`].value='founder';form.elements[`${key}_name`].value='사업평가';form.elements[`${key}_skills`].value='.agents/skills/founder/SKILL.md';submit(w,form);await tick();
  const body=JSON.parse(calls.find(([,o])=>o.method)[1].body);assert.equal(body.roles.founder.kind,'assess');assert.deepEqual(body.roles.founder.skills,['.agents/skills/founder/SKILL.md']);assert.ok(body.roles.plan);assert.ok(body.roles.implement);
});
test('answer action resumes waiting coordinator with actual user response',async t=>{
  const g=goal();g.status='waiting_answer';g.planning=true;g.steps=[{task_id:'LOCAL-1',role:'plan',title:'기획',task_status:'waiting_decision'}];const {w,calls,$}=setup(t,{goals:[g]});await tick();$('message-kind').value='answer';$('message-kind').dispatchEvent(new w.Event('change'));$('message-body').value='폰은 한 칸만 이동';submit(w,$('message-form'));await tick();const body=JSON.parse(calls.find(([,o])=>o.method)[1].body);assert.equal(body.kind,'answer');assert.equal(body.target,'plan');assert.equal(body.body,'폰은 한 칸만 이동');
});
test('switching settings projects routes save explicitly and keeps unsaved changes with their project',async t=>{
  const {w,calls,$}=setup(t);await tick();const original=w.fetch;
  w.fetch=async (url,options={})=>url.includes('/api/context') ? {ok:true,json:async()=>({project:{id:'owner'},projects:[{id:'owner',name:'A',orchestration_enabled:true},{id:'other',name:'B',orchestration_enabled:true}]})} : original(url,options);
  $('open-settings').click();await tick();const picker=$('settings-project-picker');picker.value='other';picker.dispatchEvent(new w.Event('change'));await tick();
  $('settings-form').elements.implement_instructions.value='B only';picker.value='owner';picker.dispatchEvent(new w.Event('change'));await tick();assert.equal(picker.value,'other');assert.equal($('settings-form').elements.implement_instructions.value,'B only');
  submit(w,$('settings-form'));await tick();assert.equal(calls.find(([,o])=>o.method)[0],'/api/team/settings?project=other');
});
test('Crewset preset adds canonical role guidance without replacing configured agents',async t=>{
  const {w,calls,$}=setup(t);await tick();$('open-settings').click();await tick();
  $('settings-form').elements.implement_model.value='my-current-model';$('add-crewset-role').click();
  assert.equal($('role-fields').children.length,4);assert.equal($('settings-form').elements.frontend_web_instructions.value,'Crewset 웹 책임');
  $('settings-form').elements.frontend_web_model.value='my-web-model';$('add-crewset-role').click();assert.equal($('role-fields').children.length,4);assert.match($('settings-error').textContent,/기존 설정/);
  submit(w,$('settings-form'));await tick();const body=JSON.parse(calls.find(([,o])=>o.method)[1].body);
  assert.equal(body.roles.implement.model,'my-current-model');assert.equal(body.roles.frontend_web.model,'my-web-model');assert.equal(body.roles.frontend_web.kind,'implement');
});
test('channel load delay disables actions and never sends old goal with new project',async t=>{
  const b={...goal('GOAL-B'),project_id:'other'};const {w,$,calls}=setup(t,{goals:[goal(),b]});await tick();const original=w.fetch;let resolve;
  w.fetch=(url,options={})=>url.includes('/GOAL-B/chat')&&!options.method?new Promise(r=>{resolve=()=>r({ok:true,json:async()=>({goal:b,settings:policy(),execution_enabled:true,runs:[],messages:[]})});}):original(url,options);
  $('channels').querySelector('[data-goal="GOAL-B"]').click();await tick();assert.equal($('run-action').disabled,true);assert.equal($('send-message').disabled,true);
  $('run-action').dispatchEvent(new w.Event('click'));$('message-body').value='B note';submit(w,$('message-form'));await tick();assert.equal(calls.filter(([,o])=>o.method).length,0);
  resolve();await tick();$('run-action').click();await tick();assert.equal(calls.find(([,o])=>o.method)[0],'/api/goals/GOAL-B/start?project=other');
});
test('settings load delay blocks stale project save and late response cannot replace current form',async t=>{
  const {w,$,calls}=setup(t);await tick();const original=w.fetch;let resolveB;let delay=false;
  w.fetch=(url,options={})=>{
    if(url.includes('/api/context'))return Promise.resolve({ok:true,json:async()=>({project:{id:'owner'},projects:[{id:'owner',name:'A',orchestration_enabled:true},{id:'other',name:'B',orchestration_enabled:true}]})});
    if(delay&&url.includes('/api/team/settings')&&url.includes('project=other')&&!options.method)return new Promise(r=>{resolveB=()=>r({ok:true,json:async()=>({...policy(),roles:{...policy().roles,implement:{...policy().roles.implement,model:'B-model'}}})});});
    return original(url,options);
  };
  $('open-settings').click();await tick();delay=true;const picker=$('settings-project-picker');picker.value='other';picker.dispatchEvent(new w.Event('change'));await tick();
  assert.equal($('save-settings').disabled,true);submit(w,$('settings-form'));await tick();assert.equal(calls.filter(([,o])=>o.method).length,0);
  picker.value='owner';picker.dispatchEvent(new w.Event('change'));await tick();assert.equal($('settings-form').elements.implement_model.value,'');resolveB();await tick();assert.equal(picker.value,'owner');assert.equal($('settings-form').elements.implement_model.value,'');
  submit(w,$('settings-form'));await tick();assert.equal(calls.find(([,o])=>o.method)[0],'/api/team/settings?project=owner');
});
