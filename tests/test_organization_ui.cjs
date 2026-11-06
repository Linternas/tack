const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const Module=require('node:module');
const original=Module._load;
Module._load=function(name,...args){return name==='canvas'?{}:original.call(this,name,...args)};
const {JSDOM}=Module.createRequire(require.resolve('jest-environment-jsdom',{paths:[process.env.AGENT_WORK_JSDOM_ROOT||process.cwd()]}))('jsdom');
Module._load=original;
const tick=()=>new Promise(setImmediate);
const copy=value=>JSON.parse(JSON.stringify(value));
function setup(t,options={}){
  const dom=new JSDOM('<body><p id="workspace-error" hidden></p><main id="root"></main></body>',{url:'http://localhost/team',runScripts:'outside-only'});
  t.after(()=>dom.window.close());
  const w=dom.window;
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
  const group={id:'team',name:'체스팀',kind:'product',members:['p','d'],paths:['apps/chess'],support_teams:[],max_parallel:2,max_tokens:100000};
  const data={execution_enabled:options.enabled!==false,organization:{version:4,teams:{team:group}},settings:{roles:{p:{name:'기획',kind:'plan'},d:{name:'개발',kind:'implement'}}}};
  const entry={goal_id:'goal',team_name:'체스팀',title:'계산기 <img src=x>',acceptance:'사칙연산',decision:'pending',tokens:42,reports:[{employee:'d',name:'개발',title:'구현',body:'<script>unsafe()</script>',files:['app.py']}],verification:[{argv:['python3','-m','unittest'],status:'succeeded'}],links:[]};
  const demos=[{id:'demo',title:'첫 발표',version:2,entries:[entry]}];
  const calls=[];
  const actions={
    api:async(url,payload)=>{calls.push([url,payload]);if(options.api)return options.api(url,payload,{data,demos});if(url==='/api/organization')return copy(data.organization);if(url==='/api/team/settings')return copy(data.settings);if(url==='/api/demo-days'&&!payload)return {demos:copy(demos)};return {};},
    refresh:async()=>calls.push(['refresh']),redraw:()=>calls.push(['redraw']),selectTeam:id=>calls.push(['selectTeam',id]),mission:id=>calls.push(['mission',id]),goal:id=>calls.push(['goal',id])
  };
  w.eval(fs.readFileSync(path.join(__dirname,'../agent_work/assets/organization.js'),'utf8'));
  const ui=w.TackOrganization.mount(actions);ui.update(data);
  return {w,ui,data,demos,calls,root:w.document.getElementById('root'),find:text=>[...w.document.querySelectorAll('button')].find(b=>b.textContent===text)};
}

test('team cards use literal text and exact team identity',async t=>{
  const s=setup(t);s.data.organization.teams.team.name='<img src=x>';s.ui.teams(s.root);
  assert.equal(s.root.querySelector('img'),null);assert.match(s.root.textContent,/<img src=x>/);
  s.find('목표 맡기기').click();await tick();assert.deepEqual(s.calls,[['mission','team']]);
});

test('team edit loads the latest group together with its version',async t=>{
  const s=setup(t);await s.ui.edit({...s.data.organization.teams.team,name:'과거 이름',paths:['old/path']});
  assert.equal(s.w.document.querySelector('[name=name]').value,'체스팀');
  assert.equal(s.w.document.querySelector('[name=paths]').value,'apps/chess');
  s.w.document.querySelector('form').dispatchEvent(new s.w.Event('submit',{cancelable:true}));await tick();
  const save=s.calls.find(([url])=>url==='/api/organization/teams/team');assert.equal(save[1].version,4);assert.equal(save[1].name,'체스팀');
});

test('failed team creation keeps the draft and prevents duplicate submissions',async t=>{
  let rejectSave;
  const s=setup(t,{api:async(url,payload,{data})=>{if(url==='/api/organization')return copy(data.organization);if(url==='/api/team/settings')return copy(data.settings);return new Promise((resolve,reject)=>{rejectSave=reject;});}});
  await s.ui.edit();const form=s.w.document.querySelector('form');form.elements.name.value='새로운 팀';form.elements.paths.value='apps/new';
  form.dispatchEvent(new s.w.Event('submit',{cancelable:true}));form.dispatchEvent(new s.w.Event('submit',{cancelable:true}));
  assert.equal(s.calls.filter(([url])=>url==='/api/organization/teams').length,1);
  rejectSave(Error('경로가 겹칩니다.'));await tick();assert.equal(form.elements.name.value,'새로운 팀');assert.match(form.textContent,/경로가 겹칩니다/);assert.equal(form.querySelector('button[type=submit]')?.disabled||false,false);
});

test('demo reports preserve evidence as text and require revision details',async t=>{
  const s=setup(t);await s.ui.demos(s.root);
  assert.equal(s.root.querySelector('script'),null);assert.match(s.root.textContent,/<script>unsafe/);
  s.find('수정 요청·팀에 전달').click();await tick();assert.equal(s.calls.filter(([url])=>url.endsWith('/respond')).length,0);assert.match(s.w.document.getElementById('workspace-error').textContent,/수정할 내용/);
});

for(const [decision,label]of [['accept','결과 수락'],['revise','수정 요청·팀에 전달'],['hold','보류']]){
  test('demo '+decision+' sends the evidence version and refreshes',async t=>{
    const s=setup(t);await s.ui.demos(s.root);const feedback=s.root.querySelector('textarea');feedback.value='수정 의견';feedback.dispatchEvent(new s.w.Event('input'));
    s.find(label).click();await tick();const payload=s.calls.find(([url])=>url.endsWith('/respond'))[1];assert.deepEqual(copy(payload),{version:2,goal_id:'goal',decision,feedback:'수정 의견'});assert.ok(s.calls.some(([url])=>url==='refresh'));assert.ok(s.calls.some(([url])=>url==='redraw'));
  });
}

test('pending demo decision blocks duplicate actions and restores controls on failure',async t=>{
  let rejectSave;
  const s=setup(t,{api:async(url,payload,{demos})=>payload?new Promise((resolve,reject)=>{rejectSave=reject;}):{demos:copy(demos)}});
  await s.ui.demos(s.root);s.find('결과 수락').click();await tick();assert.equal(s.find('보류').disabled,true);
  s.find('보류').click();await tick();assert.equal(s.calls.filter(([url])=>url.endsWith('/respond')).length,1);
  rejectSave(Error('발표 상태가 변경됐습니다.'));await tick();assert.equal(s.find('보류').disabled,false);assert.match(s.w.document.getElementById('workspace-error').textContent,/상태가 변경/);
});

test('late demo response cannot overwrite a different office view',async t=>{
  let resolve;
  const s=setup(t,{api:()=>new Promise(done=>{resolve=done;})});s.w.document.body.dataset.officeView='demoday';
  const pending=s.ui.demos(s.root);s.w.document.body.dataset.officeView='board';s.root.textContent='업무 보드';resolve({demos:copy(s.demos)});await pending;assert.equal(s.root.textContent,'업무 보드');
});

test('newer demo request wins and drafts survive navigation',async t=>{
  const pending=[];const s=setup(t,{api:()=>new Promise(resolve=>pending.push(resolve))});
  const first=s.ui.demos(s.root),second=s.ui.demos(s.root);pending[1]({demos:copy(s.demos)});await second;
  const feedback=s.root.querySelector('textarea');feedback.value='이어 쓸 의견';feedback.dispatchEvent(new s.w.Event('input'));
  pending[0]({demos:[]});await first;assert.match(s.root.textContent,/계산기/);
  const third=s.ui.demos(s.root);pending[2]({demos:copy(s.demos)});await third;assert.equal(s.root.querySelector('textarea').value,'이어 쓸 의견');
});

test('readonly organization keeps team and demo records readable',async t=>{
  const s=setup(t,{enabled:false});s.ui.teams(s.root);assert.equal(s.find('+ 팀 만들기').disabled,true);assert.equal(s.find('설정').disabled,true);assert.equal(s.find('사무실 열기').disabled,false);
  await s.ui.demos(s.root);for(const text of ['결과 수락','보류','수정 요청·팀에 전달','검증된 결과로 데모데이 열기'])assert.equal(s.find(text).disabled,true);
});


test('delayed presentation and session changes remove stale feedback controls immediately',async t=>{
  const pending=[];
  let initial=true;
  const s=setup(t,{api:async(url,payload,{demos})=>{
    if(initial){initial=false;return {demos:copy(demos)};}
    return new Promise(resolve=>pending.push(resolve));
  }});
  const secondEntry={...copy(s.demos[0].entries[0]),goal_id:'second-goal',title:'두 번째 발표'};
  s.demos[0].entries.push(secondEntry);
  s.demos.push({...copy(s.demos[0]),id:'second-demo',title:'다음 데모데이'});
  await s.ui.demos(s.root);
  const firstFeedback=s.root.querySelector('textarea');
  firstFeedback.value='첫 발표 의견';firstFeedback.dispatchEvent(new s.w.Event('input'));
  s.find('다음 팀 →').click();await tick();
  assert.equal(firstFeedback.isConnected,false);
  assert.equal(s.root.querySelector('textarea'),null);
  assert.equal(s.find('수정 요청·팀에 전달'),undefined);
  pending.shift()({demos:copy(s.demos)});await tick();
  assert.match(s.root.textContent,/두 번째 발표/);
  assert.equal(s.root.querySelector('textarea').value,'');
  const picker=s.root.querySelector('.demo-picker');
  const previousFeedback=s.root.querySelector('textarea');
  picker.value='second-demo';picker.dispatchEvent(new s.w.Event('change'));
  assert.equal(previousFeedback.isConnected,false);
  assert.equal(s.root.querySelector('textarea'),null);
  assert.match(s.root.textContent,/불러오는 중/);
  pending.shift()({demos:copy(s.demos)});await tick();
  assert.equal(s.root.querySelector('.demo-picker').value,'second-demo');
  assert.match(s.root.textContent,/계산기/);
  assert.equal(s.root.querySelector('textarea').value,'');
});
