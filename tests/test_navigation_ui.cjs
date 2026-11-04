const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module');const original=Module._load;Module._load=function(name,...args){return name==='canvas'?{}:original.call(this,name,...args)};const {JSDOM}=Module.createRequire(require.resolve('jest-environment-jsdom',{paths:[process.env.AGENT_WORK_JSDOM_ROOT||process.cwd()]}))('jsdom');Module._load=original;
const asset=n=>fs.readFileSync(path.join(__dirname,'../agent_work/assets',n),'utf8');const tick=async()=>{await new Promise(setImmediate);await new Promise(setImmediate)};
function setup(t,html,enabled=true){const dom=new JSDOM(html,{url:'http://127.0.0.1/?project=fixture',runScripts:'outside-only'});t.after(()=>dom.window.close());dom.window.fetch=async()=>({ok:true,json:async()=>({project:{id:'fixture',name:'Fixture'},projects:[],orchestration_enabled:enabled})});return dom.window;}

const files=['index.html','qa.html','usage.html','goals.html','workforce.html','sessions.html','projects.html'];
const routes=['/team','/','/sessions','/qa','/projects','/documents','/usage'];
test('every page keeps the same ordered SVG navigation in Core and orchestration',async t=>{
 for(const enabled of [true,false])for(const file of files){
  const w=setup(t,asset(file),enabled);w.eval(asset('common.js'));await tick();
  const links=[...w.document.querySelectorAll('.sidebar .nav-item')];
  assert.deepEqual(links.map(a=>new URL(a.href).pathname),routes,file);
  for(const link of links){const svg=link.querySelector('.nav-icon svg');assert.ok(svg,file);assert.equal(svg.getAttribute('viewBox'),'0 0 24 24');assert.equal(svg.getAttribute('stroke-width'),'1.8');assert.equal(svg.getAttribute('stroke'),'currentColor');assert.ok(link.getAttribute('aria-label'));assert.equal(new URL(link.href).searchParams.get('project'),'fixture');}
  assert.equal(links[0].getAttribute('aria-disabled'),enabled?null:'true');
  assert.equal(w.document.querySelectorAll('#project-picker').length,asset(file).includes('id="project-picker"')?1:0);
 }
});
test('board classifications stay inside the board and the project picker survives',async t=>{
 const w=setup(t,asset('index.html'));const picker=w.document.getElementById('project-picker');w.eval(asset('common.js'));await tick();
 assert.ok(w.document.querySelector('.board #navigation'));assert.equal(w.document.querySelector('.sidebar #navigation'),null);assert.equal(w.document.getElementById('project-picker'),picker);assert.ok(w.document.querySelector('main #project-picker'));
});
test('Core keeps team entry visible and blocks unsupported navigation',async t=>{
 const w=setup(t,asset('index.html'),false);w.eval(asset('common.js'));await tick();const link=w.document.querySelector('.sidebar a[href^="/team"]');
 assert.match(link.title,/설치 필요/);assert.equal(link.dispatchEvent(new w.MouseEvent('click',{bubbles:true,cancelable:true})),false);
});
test('active page is reflected without changing the menu',async t=>{
 const w=setup(t,asset('workforce.html'));w.history.replaceState(null,'','/team?project=fixture');w.eval(asset('common.js'));await tick();assert.equal(w.document.querySelector('.sidebar [aria-current="page"]').getAttribute('href'),'/team?project=fixture');
});
