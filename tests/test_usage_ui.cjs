const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const Module=require('node:module');
const original=Module._load;Module._load=function(name,...args){return name==='canvas'?{}:original.call(this,name,...args)};
const {JSDOM}=Module.createRequire(require.resolve('jest-environment-jsdom',{paths:[process.env.AGENT_WORK_JSDOM_ROOT||process.cwd()]}))('jsdom');Module._load=original;
const asset=name=>fs.readFileSync(path.join(__dirname,'../agent_work/assets',name),'utf8');
const tick=async()=>{await new Promise(setImmediate);await new Promise(setImmediate)};
const summary={runs:2,total_tokens:120,total_tokens_measured_runs:2,input_tokens:null,output_tokens:null,input_tokens_measured_runs:0,output_tokens_measured_runs:0,cache_ratio:null,cache_measured_runs:0,turns:null};
const fixture=()=>({summary,groups:{role:[{name:'<img src=x>',total_tokens:120,runs:2}],goal_id:[{name:'GOAL-A'}],phase:[{name:'legacy',total_tokens:120,runs:2}]},note:'partial data',runs:[{id:'EXEC-A',task_id:'LOCAL-1',goal_id:'GOAL-A',role:'<img src=x>',phase:'legacy',model:'',adapter:'codex-exec',input_tokens:null,cached_input_tokens:null,output_tokens:null,total_tokens:120,turns:null,duration_ms:null,status:'succeeded',created_at:'2026-10-29',round_number:null}]});
const response = (data, ok=true) => ({ok, json:async()=>data});
const emptyHistory = {items:[],total:0,has_more:false};
function setup(t,fetcher){
  const dom=new JSDOM(asset('usage.html'),{url:'http://127.0.0.1/usage?project=owner',runScripts:'outside-only'});
  t.after(()=>dom.window.close());
  const w=dom.window,calls=[];
  w.workUrl=p=>{const url=new URL(p,w.location.origin);url.searchParams.set('project','owner');return url.pathname+url.search;};
  w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
  w.fetch=async url=>{
    calls.push(url);
    return fetcher?.(new URL(url,w.location.origin)) ?? response(url.startsWith('/api/executions') ? emptyHistory : fixture());
  };
  w.eval(asset('usage.js'));
  return {w,calls,$:id=>w.document.getElementById(id)};
}
test('usage shows missing values and legacy coverage without guessing costs or injecting content',async t=>{const {w,$}=setup(t);await tick();assert.match($('usage-metrics').textContent,/미수집/);assert.match($('usage-metrics').textContent,/0\/2/);assert.match($('usage-phases').textContent,/과거 실행/);assert.equal(w.document.querySelector('img'),null);assert.equal($('usage-roles').querySelector('progress').value,120);assert.match($('usage-runs').textContent,/기본 모델 · 미확인/);assert.match($('usage-runs').textContent,/120/)});
test('usage filters keep project identity and refresh preserves selection',async t=>{const {w,calls,$}=setup(t);await tick();$('usage-goal').value='GOAL-A';$('usage-goal').dispatchEvent(new w.Event('change'));await tick();assert.match(calls.at(-1),/project=owner.*goal=GOAL-A/);$('refresh-usage').click();await tick();assert.equal($('usage-goal').value,'GOAL-A');assert.match(calls.filter(url=>url.startsWith('/api/usage')).at(-1),/goal=GOAL-A/)});
test('empty and failed usage are distinguished',async t=>{let fail=false;const {$}=setup(t,url=>url.pathname==='/api/usage' ? response(fail?{error:'unavailable'}:{summary:{...summary,runs:0},groups:{role:[],goal_id:[],phase:[]},runs:[],note:''},!fail):undefined);await tick();assert.match($('usage-runs').textContent,/수집된 실행이 없습니다/);fail=true;$('refresh-usage').click();await tick();assert.equal($('usage-error').hidden,false);assert.match($('usage-error').textContent,/unavailable/)});

test('read-only history paginates within the project and opens escaped execution details', async t => {
  const run = {id:'EXEC-A',kind:'role',label:'<img src=x>',status:'succeeded',workspace:'/repo/a',updated_at:'2026-11-19T01:00:00Z',task_id:'LOCAL-1'};
  const next = {id:'GOAL-A',kind:'goal',label:'과거 목표',status:'done'};
  const {w,$,calls}=setup(t,url=>{
    if (url.pathname==='/api/executions') {
      assert.equal(url.searchParams.get('project'),'owner');
      assert.equal(url.searchParams.get('limit'),'20');
      const first=url.searchParams.get('offset')==='0';
      return response({items:first?[run]:[next],total:2,has_more:first});
    }
    if (url.pathname==='/api/executions/EXEC-A') {
      assert.equal(url.searchParams.get('project'),'owner');
      return response({item:run,details:{summary:'<b>결과</b>',verification_results:[{status:'passed'}]}});
    }
  });
  await tick();
  const list=$('execution-rows');
  assert.equal(list.querySelector('img'),null);
  assert.match(list.textContent,/<img src=x>.*성공/s);
  assert.equal(new URL(list.querySelector('a').href).searchParams.get('ticket'),'LOCAL-1');
  assert.equal(new URL(list.querySelector('a').href).searchParams.get('project'),'owner');
  assert.equal($('more-executions').hidden,false);
  $('more-executions').click();
  await tick();
  assert.equal(list.children.length,2);
  assert.equal($('more-executions').hidden,true);
  assert.match(calls.at(-1),/offset=1/);
  list.querySelector('button').click();
  await tick();
  assert.equal($('execution-detail').open,true);
  assert.equal($('execution-fields').querySelector('b'),null);
  assert.match($('execution-fields').textContent,/<b>결과<\/b>.*검증 결과.*passed/s);
});

test('missing history can retry without hiding usage', async t => {
  let fail = true;
  const {$}=setup(t,url=>url.pathname==='/api/executions' && fail ? response({error:'unavailable'},false):undefined);
  await tick();
  assert.match($('execution-status').textContent,/실행 이력 조회 실패/);
  assert.match($('usage-metrics').textContent,/수집된 총 토큰/);
  fail=false;
  $('refresh-usage').click();
  await tick();
  assert.match($('execution-status').textContent,/보존된 실행 이력이 없습니다/);
});
