const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module');const original=Module._load;Module._load=function(name,...args){return name==='canvas'?{}:original.call(this,name,...args)};const {JSDOM}=Module.createRequire(require.resolve('jest-environment-jsdom',{paths:[process.env.AGENT_WORK_JSDOM_ROOT||process.cwd()]}))('jsdom');Module._load=original;
const asset=n=>fs.readFileSync(path.join(__dirname,'../agent_work/assets',n),'utf8');const tick=async()=>{await new Promise(setImmediate);await new Promise(setImmediate)};
function setup(t,html){const dom=new JSDOM(html,{url:'http://127.0.0.1/?project=fixture',runScripts:'outside-only'});t.after(()=>dom.window.close());dom.window.fetch=async()=>({ok:true,json:async()=>({project:{id:'fixture',name:'Fixture'},projects:[]})});return dom.window;}

const files=['index.html','notes.html','qa.html','usage.html','sessions.html','projects.html'];
const groups = [
  ['문서', ['/', '/notes', '/documents']],
  ['기록', ['/sessions', '/usage']],
  ['관리', ['/qa', '/projects']],
];
test('every Core page places document routes before records and management',async t=>{
 for(const file of files){
  const w=setup(t,asset(file));w.eval(asset('common.js'));await tick();
  const navigation = [...w.document.querySelectorAll('.sidebar nav.nav-group')];
  assert.deepEqual(navigation.map(nav => [
    nav.getAttribute('aria-label'),
    [...nav.querySelectorAll('a')].map(a => new URL(a.href).pathname),
  ]), groups, file);
  const links=[...w.document.querySelectorAll('.sidebar .nav-item')];
  for(const link of links){const svg=link.querySelector('.nav-icon svg');assert.ok(svg,file);assert.equal(svg.getAttribute('viewBox'),'0 0 24 24');assert.equal(svg.getAttribute('stroke-width'),'1.8');assert.equal(svg.getAttribute('stroke'),'currentColor');assert.ok(link.getAttribute('aria-label'));assert.equal(new URL(link.href).searchParams.get('project'),'fixture');}
  assert.deepEqual([...navigation[0].querySelectorAll('a')].map(a => a.getAttribute('aria-label')), ['티켓', '노트', '보관 문서']);
  assert.equal(new Set([...navigation[0].querySelectorAll('svg')].map(svg => svg.innerHTML)).size, 3, file);
  assert.equal(w.document.querySelectorAll('#project-picker').length,asset(file).includes('id="project-picker"')?1:0);
 }
});
test('board classifications stay inside the board and the project picker survives',async t=>{
 const w=setup(t,asset('index.html'));const picker=w.document.getElementById('project-picker');w.eval(asset('common.js'));await tick();
 assert.equal(w.document.getElementById('navigation'),null);assert.equal(w.document.querySelector('#ticket-list #category-filter'),null);assert.ok(w.document.querySelector('#ticket-list #record-view'));assert.equal(w.document.getElementById('project-picker'),picker);assert.ok(w.document.querySelector('main #project-picker'));
});
test('board has no team navigation or execution activation controls', async t => {
  const w = setup(t, asset('index.html'));
  w.eval(asset('common.js'));
  await tick();
  assert.equal(w.document.querySelector('a[href^="/team"], a[href^="/goals"], #enable-orchestration, #workspace-controls'), null);
  assert.ok(w.document.querySelector('#open-worktrees'));
});

test('execution history is the active navigation item and stays scoped to the selected checkout', async t => {
  const w = setup(t, asset('usage.html'));
  const workspace = '/projects/current & active';
  w.history.replaceState(null, '', '/usage?project=fixture&workspace=' + encodeURIComponent(workspace));
  w.eval(asset('common.js'));
  await tick();

  const active = new URL(w.document.querySelector('.sidebar [aria-current="page"]').href);
  assert.equal(active.pathname, '/usage');
  assert.equal(active.searchParams.get('workspace'), workspace);
  assert.equal(new URL(w.workUrl('/api/executions?limit=20'), w.location.origin).searchParams.get('workspace'), workspace);
  const anotherProject = new URL(w.workUrl('/documents?project=other'), w.location.origin);
  assert.equal(anotherProject.searchParams.get('project'), 'other');
  assert.equal(anotherProject.searchParams.has('workspace'), false);
});

test('an archived document keeps its document navigation item active', async t => {
  const w = setup(t, '<main class="session-layout"><nav><a href="/documents">문서</a></nav></main>');
  w.history.replaceState(null, '', '/documents/DOC-1?project=fixture');
  w.eval(asset('common.js'));
  await tick();

  const active = w.document.querySelector('.sidebar [aria-current="page"]');
  assert.equal(new URL(active.href).pathname, '/documents');
  assert.equal(active.closest('nav').getAttribute('aria-label'), '문서');
  assert.equal(w.document.querySelector('main nav'), null);
});

test('switching projects clears the previous checkout and selected ticket', async t => {
  const w = setup(t, asset('index.html'));
  const location = new URL('http://127.0.0.1/?project=fixture&workspace=%2Fproject%2Fa&ticket=LOCAL-0123&view=notes');
  let destination;
  location.assign = url => { destination = url; };
  const fetch = async () => ({ok:true, json:async () => ({
    project: {id:'fixture', name:'Fixture'},
    projects: [{id:'fixture', name:'Fixture'}, {id:'other', name:'Other'}],
  })});
  require('node:vm').runInNewContext(asset('common.js'), {
    window: w, document: w.document, location, fetch, URL, URLSearchParams,
  });
  await tick();

  const picker = w.document.getElementById('project-picker');
  picker.value = 'other';
  picker.dispatchEvent(new w.Event('change'));
  assert.ok(destination);
  assert.deepEqual([...destination.searchParams], [['project', 'other'], ['view', 'notes']]);
});

test('project notes can be created, found by body, and opened for editing', async t => {
  const w = setup(t, asset('notes.html'));
  w.history.replaceState(null, '', '/notes?project=fixture');
  const d = w.document;
  const editor = d.getElementById('note-editor');
  editor.showModal = () => editor.setAttribute('open', '');
  editor.close = () => editor.removeAttribute('open');
  const form = d.getElementById('note-form');
  const context = w.fetch;
  let note;
  w.fetch = async (path, options) => {
    const url = new URL(path, w.location.origin);
    assert.equal(url.searchParams.get('project'), 'fixture');
    if (url.pathname === '/api/context') return context();
    if (options?.method === 'POST') note = {
      ...JSON.parse(options.body), id: 'NOTE-1', version: 1, updated_at: '2026-11-19T01:00:00Z'
    };
    const query = url.searchParams.get('q');
    const data = url.pathname === '/api/notes' && !options?.method
      ? {notes: note?.body.includes(query) ? [note] : [], has_more: false}
      : note;
    return {ok: true, json: async () => data};
  };
  w.eval(asset('common.js'));
  w.eval(asset('notes.js'));
  await tick();
  d.getElementById('add-note').click();
  form.elements.namedItem('title').value = '<아이디어>';
  form.elements.namedItem('body').value = '본문 키워드';
  form.dispatchEvent(new w.Event('submit', {cancelable: true}));
  await tick();
  d.getElementById('note-query').value = '키워드';
  d.getElementById('note-search').dispatchEvent(new w.Event('submit', {cancelable: true}));
  await tick();
  const button = d.querySelector('#note-rows button');
  assert.equal(button.textContent, '<아이디어>');
  button.click();
  await tick();
  assert.ok(editor.open);
  assert.equal(form.elements.namedItem('body').value, '본문 키워드');
});
