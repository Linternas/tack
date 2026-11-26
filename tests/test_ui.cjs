const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const { createRequire } = require('node:module');

const originalLoad = Module._load;
Module._load = function (name, ...args) { return name === 'canvas' ? {} : originalLoad.call(this, name, ...args); };
const requireEnvironment = createRequire(require.resolve('jest-environment-jsdom', { paths: [process.env.AGENT_WORK_JSDOM_ROOT || process.cwd()] }));
const { JSDOM } = requireEnvironment('jsdom');
Module._load = originalLoad;

const html = fs.readFileSync(path.join(__dirname, '../agent_work/assets/index.html'), 'utf8');
const script = fs.readFileSync(path.join(__dirname, '../agent_work/assets/dashboard.js'), 'utf8');
const tick = async () => { await new Promise(setImmediate); await new Promise(setImmediate); };
const queueOf = task => {
  if(['done','archived'].includes(task.status)) return task.status;
  if(task.category==='qa') return 'qa';
  if(task.category==='issue' || task.status==='needs_recheck') return 'issue';
  if(task.status==='in_progress') return 'in_progress';
  if(['blocked','waiting_api','waiting_decision'].includes(task.status) || ['api','decision'].includes(task.category)) return 'waiting';
  return 'todo';
};
const normalizeQueue = (task,payload) => {
  task.is_api_request = payload.is_api_request ?? task.is_api_request ?? (task.category==='api');
  if(payload.queue && queueOf(task)!==payload.queue) {
    if(['done','archived'].includes(payload.queue)) task.status=payload.queue;
    else {
      task.category=['qa','issue'].includes(payload.queue)?payload.queue:'task';
      task.status={qa:'todo',issue:'needs_recheck',waiting:'blocked'}[payload.queue] || payload.queue;
    }
  }
  task.queue=queueOf(task);return task;
};
const makeTask = (id, title, category, status = 'todo', options = {}) => ({
  id, title, category, status, priority: 'P2', owner: 'FE', areas: ['Scope 1'],
  page_path:'', summary: '확인할 배경', detail: '확인한 계약', next_action: '다음 확인', acceptance: '기대하는 완료',
  verification: '코드', last_checked: '2026-10-22', resolution: '', sources: [], blocked_by: [], parent_id:'', relations:[],
  qa_result: 'not_run', qa: { preconditions: '테스트 법인 준비', steps: '입력 후 재조회', expected: '값 유지', actual: '', evidence: '' },
  version: 1, created_at: '2026-10-22T00:00:00Z', updated_at: '2026-10-22T00:00:00Z', ...options,
});

async function setup(context, fixture, url = 'http://127.0.0.1:8788/') {
  const dom = new JSDOM(html, { url, runScripts: 'outside-only' });
  context.after(() => dom.window.close());
  const { window } = dom;
  window.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  window.HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); this.dispatchEvent(new window.Event('close')); };
  window.confirm = () => true;
  const clipboard = [];
  Object.defineProperty(window.navigator, 'clipboard', {
    configurable: true,
    value: { writeText: async (text) => { clipboard.push(text); } },
  });
  const records = fixture || [
    makeTask('ISSUE-001', '목록 요청 버튼', 'issue', 'needs_recheck', { priority: 'P1' }),
    makeTask('DEC-001', '담당자 정책', 'decision', 'waiting_decision'),
    makeTask('API-001', '계산 응답', 'api', 'waiting_api'),
    makeTask('QA-001', '입력 저장 QA', 'qa'),
    makeTask('QA-002', '선행 결정 필요한 QA', 'qa', 'todo', { blocked_by: ['DEC-001'] }),
    makeTask('DONE-001', '처리한 문제', 'issue', 'done', { resolution: '수정·자동 검증' }),
  ];
  const relationships = (id) => {
    const items=[];
    const add=(type,target,outgoing)=>{
      const task=records.find(t=>t.id===target);
      if(task) items.push({type,outgoing,id:task.id,title:task.title,queue:queueOf(task),status:task.status,version:task.version});
    };
    const source=records.find(t=>t.id===id);
    if(source.parent_id) add('parent',source.parent_id,true);
    for(const item of source.relations || []) add(item.type,item.task_id,true);
    for(const item of records) {
      if(item.parent_id===id) add('child',item.id,false);
      for(const link of item.relations || []) if(link.task_id===id) add({duplicate_of:'duplicated_by',superseded_by:'supersedes',relates_to:'relates_to'}[link.type],item.id,false);
    }
    return {items,counts:{}};
  };
  const calls = [];
  let forceConflict = false;
  let offline = false;
  window.fetch = async (url, options = {}) => {
    calls.push({ url, ...options });
    if (offline) throw new Error('offline');
    let status = 200;
    let data;
    const method = options.method || 'GET';
    if (url === '/api/tasks' && method === 'GET') data = { tasks: records.map(task=>({...structuredClone(task),queue:queueOf(task),is_api_request:task.is_api_request ?? (task.category==='api')})), saved_at: '2026-10-22T01:00:00Z' };
    else if (url.startsWith('/api/source')) data = { text: '<script>unsafe()</script> 원문' };
    else if (url.endsWith('/relations') && method === 'POST') {
      const payload=JSON.parse(options.body);const task=records.find(t=>t.id===url.split('/').at(-2));
      if(forceConflict || task.version!==payload.version) {status=409;data={error:'다른 창에서 관계가 변경됐습니다.'};}
      else {
        if(payload.type==='parent') task.parent_id=payload.remove?'':payload.target;
        else if(payload.type==='blocked_by') task.blocked_by=payload.remove?task.blocked_by.filter(id=>id!==payload.target):[...task.blocked_by,payload.target];
        else task.relations=payload.remove?task.relations.filter(link=>link.type!==payload.type || link.task_id!==payload.target):[...task.relations,{type:payload.type,task_id:payload.target}];
        task.version++;task.queue=queueOf(task);data={task:structuredClone(task),history:[],relationships:relationships(task.id)};
      }
    }
    else if (method === 'PATCH' || method === 'POST') {
      const payload = JSON.parse(options.body);
      const id = url.split('/').at(-1);
      const index = records.findIndex((task) => task.id === id);
      const candidate = normalizeQueue(index >= 0 ? { ...records[index], ...payload } : {category:'task',status:'todo',...payload},payload);
      if (forceConflict) { status = 409; data = { error: '다른 창에서 이미 변경됐습니다.' }; }
      else if (candidate.category === 'qa' && candidate.status === 'done' && (!['passed', 'not_applicable'].includes(candidate.qa_result) || !candidate.qa.actual.trim())) { status = 400; data = { error: 'QA 완료에는 통과 또는 해당 없음 판정과 실제 결과가 필요합니다.' }; }
      else {
        const task = index >= 0 ? {...candidate,version:records[index].version+1} : makeTask('LOCAL-0001',candidate.title,candidate.category,candidate.status,candidate);
        if (index >= 0) records[index] = task; else records.push(task);
        data = { task: structuredClone(task), history: [] };
      }
    } else {
      const task = records.find((item) => item.id === url.split('/').at(-1));
      data = { task: {...structuredClone(task),queue:queueOf(task),is_api_request:task.is_api_request ?? (task.category==='api')}, history: [{ action: 'created', note: '초기 등록', created_at: task.created_at }],relationships:relationships(task.id) };
    }
    return { ok: status < 400, status, json: async () => data };
  };
  window.eval(script);
  await tick();
  const document = window.document;
  const $ = (id) => document.getElementById(id);
  const rows = () => [...$('task-rows').querySelectorAll('[data-task-id]')].map((row) => row.dataset.taskId);
  const change = async (id, value) => { $(id).value = value; $(id).dispatchEvent(new window.Event(id === 'search' ? 'input' : 'change')); await tick(); };
  const click = async (selector) => { document.querySelector(selector).click(); await tick(); };
  const submit = async (id = 'task-form') => { $(id).dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })); await tick(); };
  return { window, document, $, rows, change, click, submit, calls, records, clipboard, conflict: () => { forceConflict = true; }, offline: () => { offline = true; } };
}

async function toggleTaskMap(ui, open = true) {
  const map = ui.document.querySelector('.task-map');
  const toggled = new Promise(resolve => map.addEventListener('toggle', resolve, {once: true}));
  map.open = open;
  await toggled;
  await tick();
  return map;
}

test('ticket reader opens beside the filtered list and returns focus to the selected title', async (t) => {
  const ui = await setup(t, undefined, 'http://127.0.0.1:8788/?project=demo&workspace=%2Fwork#board');
  await ui.change('search', '목록');
  await ui.click('.task-title[data-open="ISSUE-001"]');
  assert.equal(ui.$('detail-panel').tagName, 'SECTION');
  assert.equal(ui.document.querySelector('dialog[open]'), null);
  assert.equal(ui.$('workspace-layout').hasAttribute('data-ticket-open'), true);
  assert.equal(ui.$('detail-empty').hidden, true);
  assert.equal(ui.$('detail-content').hidden, false);
  assert.equal(ui.document.activeElement, ui.$('detail-id'));
  const title = ui.document.querySelector('.task-title[data-open="ISSUE-001"]');
  assert.equal(title.getAttribute('aria-current'), 'true');
  assert.equal(title.closest('tr').classList.contains('selected'), true);
  const headings = [...ui.$('detail-content').querySelectorAll('h3')].map(el => el.textContent.trim());
  assert.ok(headings.indexOf('다음 할 일') < headings.indexOf('상세 / 현재 제약'));
  assert.ok(headings.indexOf('완료·확인 기준') < headings.indexOf('작업 관계'));
  assert.equal(new URL(ui.window.location.href).searchParams.get('ticket'), 'ISSUE-001');

  ui.$('detail-id').dispatchEvent(new ui.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.equal(ui.$('workspace-layout').hasAttribute('data-ticket-open'), false);
  assert.equal(ui.$('detail-content').hidden, true);
  assert.equal(ui.$('detail-empty').hidden, false);
  assert.equal(ui.document.activeElement, title);
  assert.equal(title.hasAttribute('aria-current'), false);
  assert.equal(ui.$('search').value, '목록');
  assert.deepEqual(ui.rows(), ['ISSUE-001']);
  assert.equal(ui.window.location.search, '?project=demo&workspace=%2Fwork');
  assert.equal(ui.window.location.hash, '#board');
});

test('slash opens mobile search and keeps a visible desktop reader open', async (t) => {
  const ui = await setup(t);
  const slash = (target) => target.dispatchEvent(new ui.window.KeyboardEvent('keydown', {
    key: '/', bubbles: true, cancelable: true,
  }));
  await ui.click('.task-title[data-open="ISSUE-001"]');
  assert.equal(slash(ui.$('detail-id')), false);
  assert.equal(ui.document.activeElement, ui.$('search'));
  assert.equal(ui.$('workspace-layout').hasAttribute('data-ticket-open'), true);

  const getStyle = ui.window.getComputedStyle.bind(ui.window);
  ui.window.getComputedStyle = (element) => element === ui.$('ticket-list') &&
      ui.$('workspace-layout').hasAttribute('data-ticket-open') ? {display: 'none'} : getStyle(element);
  ui.$('detail-title').setAttribute('contenteditable', 'true');
  assert.equal(slash(ui.$('detail-title')), true);
  await ui.click('[data-edit="ISSUE-001"]');
  assert.equal(slash(ui.$('detail-id')), true);
  await ui.click('#cancel-edit');
  assert.equal(ui.$('workspace-layout').hasAttribute('data-ticket-open'), true);
  assert.equal(slash(ui.$('detail-id')), false);
  assert.equal(ui.$('workspace-layout').hasAttribute('data-ticket-open'), false);
  assert.equal(ui.document.activeElement, ui.$('search'));
});

test('offscreen selected rows are revealed and can scroll into view when closing the reader', async (t) => {
  const ui = await setup(t);
  const title = ui.document.querySelector('.task-title[data-open="ISSUE-001"]');
  ui.$('ticket-list').getBoundingClientRect = () => ({top: 0, bottom: 400});
  ui.$('ticket-list').querySelector('.table-scroll').getBoundingClientRect = () => ({top: 90, bottom: 390});
  title.getBoundingClientRect = () => ({top: 430, bottom: 460});
  let revealOptions;
  title.scrollIntoView = options => { revealOptions = options; };
  await ui.click('.task-title[data-open="ISSUE-001"]');
  assert.equal(revealOptions?.block, 'nearest');
  const focus = title.focus.bind(title);
  let focusOptions;
  title.focus = options => { focusOptions = options; focus(options); };
  await ui.click('#close-detail');
  assert.equal(focusOptions?.preventScroll, false);
  assert.equal(ui.document.activeElement, title);
});

test('a completed ticket deep link highlights its row in the completed view', async (t) => {
  const ui = await setup(t, undefined, 'http://127.0.0.1:8788/?ticket=DONE-001');
  assert.equal(ui.$('record-view').value, 'done');
  assert.deepEqual(ui.rows(), ['DONE-001']);
  assert.equal(ui.document.querySelector('.task-title[aria-current="true"]').dataset.open, 'DONE-001');
  assert.equal(ui.$('detail-title').textContent, '처리한 문제');
  await ui.change('search', '보이지 않는 제목');
  await ui.click('#close-detail');
  assert.equal(ui.document.activeElement, ui.$('search'));
  assert.equal(ui.$('search').value, '보이지 않는 제목');
});

test('an archived ticket deep link selects the archived list', async (t) => {
  const archived = makeTask('LOCAL-0041', '보관한 정책', 'task', 'archived');
  const ui = await setup(t, [archived], 'http://127.0.0.1:8788/?ticket=LOCAL-0041');
  assert.equal(ui.$('record-view').value, 'archived');
  assert.deepEqual(ui.rows(), ['LOCAL-0041']);
  assert.equal(ui.document.querySelector('.task-title[aria-current="true"]').dataset.open, 'LOCAL-0041');
  assert.equal(ui.$('detail-title').textContent, '보관한 정책');
});

test('a newer ticket detail keeps the document list and status counts in sync', async (t) => {
  const ui = await setup(t);
  const issue = ui.records.find(task => task.id === 'ISSUE-001');
  issue.title = '다른 세션이 저장한 제목';
  issue.status = 'done';
  issue.version++;
  await ui.click('[data-open="ISSUE-001"]');
  assert.equal(ui.$('detail-title').textContent, issue.title);
  assert.equal(ui.$('record-view').value, 'all');
  assert.equal(ui.rows().includes('ISSUE-001'), false);
  assert.equal(ui.document.querySelector('[data-queue="done"] .metric-number').textContent, '2');
});

test('ticket requests that resolve after switching or closing cannot change the reader', async (t) => {
  const ui = await setup(t);
  const original = ui.window.fetch;
  let settle;
  ui.window.fetch = (url, options) => url === '/api/tasks/ISSUE-001'
    ? new Promise(resolve => { settle = () => resolve(original(url, options)); })
    : original(url, options);
  await ui.click('[data-open="ISSUE-001"]');
  assert.equal(ui.$('detail-content').getAttribute('aria-busy'), 'true');
  await ui.click('[data-open="QA-001"]');
  settle();
  await tick();
  assert.equal(ui.$('detail-title').textContent, '입력 저장 QA');

  await ui.click('[data-open="ISSUE-001"]');
  await ui.click('#close-detail');
  settle();
  await tick();
  assert.equal(ui.$('detail-content').childElementCount, 0);
  assert.equal(ui.$('detail-content').getAttribute('aria-busy'), null);
  assert.equal(ui.$('detail-empty').hidden, false);
});

test('archiving a ticket updates the list without interrupting a different document', async (t) => {
  const ui = await setup(t);
  const original = ui.window.fetch;
  let settle;
  ui.window.fetch = (url, options) => url === '/api/tasks/ISSUE-001' && options.method === 'PATCH'
    ? new Promise(resolve => { settle = () => resolve(original(url, options)); })
    : original(url, options);
  await ui.click('[data-open="ISSUE-001"]');
  await ui.click('[data-archive="ISSUE-001"]');
  await ui.click('[data-open="QA-001"]');
  settle();
  await tick();
  assert.equal(ui.$('detail-title').textContent, '입력 저장 QA');
  assert.equal(ui.rows().includes('ISSUE-001'), false);
  assert.equal(ui.records.find(task => task.id === 'ISSUE-001').queue, 'archived');
});

test('refresh preserves a draft and an open map for the same ticket version', async (t) => {
  const ui = await setup(t);
  const original = ui.window.fetch;
  let storedMap = null;
  let mapReads = 0;
  ui.window.fetch = (url, options) => {
    if (url !== '/api/tasks/ISSUE-001/map') return original(url, options);
    mapReads++;
    return Promise.resolve({ok: true, json: async () => ({map: structuredClone(storedMap)})});
  };
  await ui.click('[data-open="ISSUE-001"]');
  const target = ui.$('relation-target');
  target.value = 'QA-001';
  const map = await toggleTaskMap(ui);
  assert.match(map.textContent, /등록된 코드 영향 범위와 화면 흐름이 없습니다/);
  assert.equal(mapReads, 1);
  storedMap = {revision: 'a'.repeat(40), stale: false, version: 1,
    nodes: [{id: 'route', kind: 'route', label: '새 코드 지도'}], edges: [], flows: []};
  await ui.click('#refresh');
  assert.equal(mapReads, 2);
  assert.equal(ui.$('relation-target'), target);
  assert.equal(target.value, 'QA-001');
  assert.equal(ui.document.querySelector('.task-map'), map);
  assert.equal(map.open, true);
  assert.match(map.textContent, /새 코드 지도/);
  await ui.change('task-map-node', 'route');
  const mapSelect = ui.$('task-map-node');

  const policy = ui.records.find(task => task.id === 'DEC-001');
  policy.relations.push({type: 'relates_to', task_id: 'ISSUE-001'});
  await ui.click('#refresh');
  assert.match(ui.document.querySelector('.relation-list').textContent, /DEC-001.*담당자 정책/);
  assert.equal(ui.$('relation-target'), target);
  assert.equal(target.value, 'QA-001');
  assert.equal(ui.document.querySelector('.task-map'), map);
  assert.equal(map.open, true);
  assert.equal(mapReads, 3);
  assert.equal(ui.$('task-map-node'), mapSelect);
  assert.equal(mapSelect.value, 'route');
  await ui.click('[data-copy-task]');
  assert.match(ui.clipboard[0], /관련 작업 · DEC-001/);

  await toggleTaskMap(ui, false);
  storedMap.stale = true;
  await ui.click('#refresh');
  assert.equal(mapReads, 3);
  await toggleTaskMap(ui);
  assert.equal(mapReads, 4);
  assert.match(map.textContent, /현재 작업본이 저장 기준과 다릅니다/);

  const issue = ui.records.find(task => task.id === 'ISSUE-001');
  issue.title = '다른 세션이 저장한 제목';
  issue.version++;
  await ui.click('#refresh');
  assert.equal(ui.$('detail-title').textContent, issue.title);
});

test('a map refresh supersedes an older response and ignores responses after closing', async (t) => {
  const ui = await setup(t);
  const original = ui.window.fetch;
  const responses = [];
  ui.window.fetch = (url, options) => url === '/api/tasks/ISSUE-001/map'
    ? new Promise(resolve => { responses.push(map => resolve({ok: true, json: async () => ({map})})); })
    : original(url, options);
  await ui.click('[data-open="ISSUE-001"]');
  const map = await toggleTaskMap(ui);
  await ui.click('#refresh');
  assert.equal(responses.length, 2);
  responses[1]({revision: 'a'.repeat(40), stale: false, version: 1, nodes: [], edges: [], flows: []});
  responses[0](null);
  await tick();
  assert.match(map.textContent, /기준 커밋/);
  await ui.click('#refresh');
  await toggleTaskMap(ui, false);
  responses[2](null);
  await tick();
  assert.match(map.textContent, /기준 커밋/);
  await toggleTaskMap(ui);
  assert.equal(responses.length, 4);
  responses[3](null);
  await tick();
  assert.match(map.textContent, /등록된 코드 영향 범위와 화면 흐름이 없습니다/);
});

test('refresh retries a failed detail with the same ticket version', async (t) => {
  const ui = await setup(t);
  await ui.click('[data-open="ISSUE-001"]');
  const original = ui.window.fetch;
  ui.window.fetch = (url, options) => url === '/api/tasks/ISSUE-001'
    ? Promise.reject(new Error('상세 연결 실패'))
    : original(url, options);
  await ui.click('#refresh');
  assert.match(ui.$('detail-content').textContent, /상세 연결 실패/);
  ui.window.fetch = original;
  await ui.click('#refresh');
  assert.equal(ui.$('detail-title').textContent, '목록 요청 버튼');
});

test('a list refresh taken before a successful archive is fetched again', async (t) => {
  const ui = await setup(t);
  const original = ui.window.fetch;
  let settleSnapshot, listReads = 0;
  ui.window.fetch = (url, options) => {
    const snapshot = original(url, options);
    return url === '/api/tasks' && ++listReads === 1
      ? new Promise(resolve => { settleSnapshot = () => resolve(snapshot); })
      : snapshot;
  };
  await ui.click('[data-open="ISSUE-001"]');
  await ui.click('#refresh');
  await ui.click('[data-archive="ISSUE-001"]');
  await ui.click('[data-open="DEC-001"]');
  settleSnapshot();
  await tick();
  assert.equal(listReads, 2);
  assert.equal(ui.rows().includes('ISSUE-001'), false);
  assert.equal(ui.$('detail-title').textContent, '담당자 정책');
});

test('ticket map loads on demand, filters both directions and labels evidence', async (t) => {
  const ui = await setup(t);
  const original = ui.window.fetch;
  let requests = 0;
  const revision = 'b'.repeat(40);
  ui.window.fetch = (url, options) => {
    if (url !== '/api/tasks/ISSUE-001/map') return original(url, options);
    requests++;
    return Promise.resolve({ok: true, json: async () => ({map: {
      revision, stale: true, version: 1,
      nodes: [
        {id: 'route', kind: 'route', label: '결재함', ref: {path: 'src/page.tsx', line: 12}},
        {id: 'hook', kind: 'hook', label: 'useReview'},
        {id: 'api', kind: 'api', label: '<img src=x onerror=alert(1)>'},
      ],
      edges: [
        {from: 'route', to: 'hook', label: '조회'},
        {from: 'hook', to: 'api', label: '요청'},
        {from: 'route', to: 'api', label: '다른 호출'},
        {from: 'api', to: 'route', label: '되돌림'},
      ],
      flows: [
        {step: '화면 진입', condition: '권한 있음', outcome: '결재함 열림', kind: 'spec', evidence: '기획안'},
        {step: '권한 확인', condition: '조회 성공', outcome: '목록 표시', kind: 'code', ref: {path: 'src/page.tsx', line: 18}},
        {step: '빈 목록', condition: '준비된 법인', outcome: '내 차례 0건', kind: 'browser', evidence: 'QA 기록'},
      ],
    }})});
  };
  await ui.click('[data-open="ISSUE-001"]');
  assert.equal(requests, 0);
  const map = await toggleTaskMap(ui);
  assert.equal(requests, 1);
  assert.match(map.textContent, new RegExp(revision));
  assert.match(map.textContent, /현재 작업본이 저장 기준과 다릅니다/);
  assert.match(map.textContent, /src\/page.tsx:12/);
  assert.deepEqual([...map.querySelectorAll('.task-map-flow .badge')].map(badge => badge.textContent),
    ['기획 기준', '코드 확인', '브라우저 실측']);
  assert.equal(map.querySelector('img'), null);
  assert.match(map.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal(map.querySelectorAll('#task-map-graph [data-graph-node]').length, 3);
  assert.equal(map.querySelectorAll('#task-map-graph .map-edge[marker-end]').length, 4);
  assert.equal(map.querySelectorAll('.task-map-flow-lane').length, 3);
  assert.match(map.querySelector('.task-map-flow-lane').textContent, /결재함 열림/);
  map.querySelector('#task-map-graph [data-graph-node="hook"]')
    .dispatchEvent(new ui.window.MouseEvent('click', {bubbles: true, cancelable: true}));
  assert.equal(ui.$('task-map-node').value, 'hook');
  assert.deepEqual([...map.querySelectorAll('#task-map-graph .map-edge')].map(edge => [edge.dataset.from, edge.dataset.to]),
    [['route', 'hook'], ['hook', 'api']]);
  const rows = [...map.querySelectorAll('tbody tr')];
  assert.deepEqual(rows.map(row => row.cells[1].textContent), ['조회', '요청']);
  assert.match(rows[0].cells[2].textContent, /useReview/);
  assert.match(rows[1].cells[0].textContent, /useReview/);
  await toggleTaskMap(ui, false);
  await toggleTaskMap(ui);
  assert.equal(requests, 1);
});

test('ticket map links API to related flows and tests in both directions', async (t) => {
  const ui = await setup(t);
  const original = ui.window.fetch;
  ui.window.fetch = (url, options) => {
    if (url !== '/api/tasks/ISSUE-001/map') return original(url, options);
    return Promise.resolve({ok: true, json: async () => ({map: {
      revision: 'b'.repeat(40), stale: false, version: 1,
      nodes: [
        {id:'hook',kind:'hook',label:'useSentReviewInbox',ref:{path:'src/hooks.ts',line:42}},
        {id:'client',kind:'api',label:'reviewInboxApi.fetch',ref:{path:'src/client.ts',line:27}},
        {id:'endpoint',kind:'api',label:'GET review-rows'},
        {id:'test',kind:'test',label:'정렬 및 기간 테스트'},
        {id:'drawer',kind:'ui',label:'상세 창'},
      ],
      edges: [
        {from:'hook',to:'client',label:'조회'},
        {from:'client',to:'endpoint',label:'전송'},
        {from:'client',to:'test',label:'검증'},
      ],
      flows: [
        {step:'올린 요청',condition:'탭 선택',outcome:'목록 조회',kind:'code',node_ids:['hook','client','endpoint','test']},
        {step:'상세 열기',condition:'행 선택',outcome:'상세 표시',kind:'code',node_ids:['drawer']},
        {step:'조회 훅 준비',condition:'탭 진입',outcome:'조회 조건 확인',kind:'code',node_ids:['hook']},
      ],
    }})});
  };
  await ui.click('[data-open="ISSUE-001"]');
  const map = await toggleTaskMap(ui);
  map.querySelector('[data-graph-node="endpoint"]').dispatchEvent(new ui.window.MouseEvent('click', {bubbles: true}));
  assert.match(ui.$('task-map-focus').textContent, /관련 화면 분기 1개.*올린 요청/s);
  assert.match(ui.$('task-map-focus').textContent, /관련 테스트 1개.*정렬 및 기간 테스트/s);
  assert.ok(map.querySelector('[data-graph-node="test"].linked'));
  assert.ok(map.querySelector('[data-from="client"][data-to="test"]'));
  assert.equal(map.querySelectorAll('[data-map-flow-row].linked').length, 1);
  await ui.click('.task-map-flow [data-map-flow="0"]');
  assert.equal(map.querySelector('.task-map-flow [data-map-flow="0"]').getAttribute('aria-pressed'), 'true');
  assert.match(map.querySelector('[data-map-flow-row="0"] .task-map-flow-links').textContent,
    /useSentReviewInbox.*reviewInboxApi.fetch.*GET review-rows/);
  assert.ok(map.querySelector('[data-graph-node="hook"].linked'));
  assert.equal(map.querySelector('[data-graph-node="drawer"]'), null);
  const evidence = ui.$('task-map-edges');
  evidence.closest('details').open = true;
  assert.match(evidence.querySelector('tbody').textContent, /src\/hooks\.ts:42/);
  assert.equal(evidence.querySelector('ul'), null);
  await ui.click('.task-map-flow [data-map-flow="2"]');
  assert.ok(map.querySelector('[data-graph-node="hook"].linked'));
  assert.equal(evidence.querySelector('table'), null);
  assert.deepEqual([...evidence.querySelectorAll('code')].map(item => item.textContent), ['src/hooks.ts:42']);
  await ui.click('[data-map-clear]');
  assert.equal(ui.$('task-map-focus').hidden, true);
  assert.equal(map.querySelectorAll('[data-map-flow-row].linked').length, 0);
  assert.ok(map.querySelector('[data-graph-node="drawer"]'));
});

test('ticket map retries after error and ignores a response for another open ticket', async (t) => {
  const ui = await setup(t);
  const original = ui.window.fetch;
  let settle;
  let requests = 0;
  ui.window.fetch = (url, options) => {
    if (url === '/api/tasks/QA-001/map') return Promise.resolve({ok: true, json: async () => ({map: null})});
    if (url !== '/api/tasks/ISSUE-001/map') return original(url, options);
    requests++;
    if (requests === 1) return Promise.resolve({ok: false, status: 503, json: async () => ({error: '조회 실패'})});
    return new Promise(resolve => { settle = resolve; });
  };
  await ui.click('[data-open="ISSUE-001"]');
  const first = await toggleTaskMap(ui);
  assert.match(first.querySelector('[role=alert]').textContent, /조회 실패/);
  await ui.click('.task-map button');
  assert.match(first.textContent, /불러오는 중/);
  await ui.click('[data-open="QA-001"]');
  const second = await toggleTaskMap(ui);
  settle({ok: true, json: async () => ({map: {
    revision: 'a'.repeat(40), stale: false, nodes: [], edges: [], flows: [],
  }})});
  await tick();
  assert.match(second.textContent, /등록된 코드 영향 범위와 화면 흐름이 없습니다/);
  assert.doesNotMatch(first.textContent, /기준 커밋/);
});

test('initial view separates ongoing work from completed history', async (t) => {
  const ui = await setup(t);
  assert.equal(ui.rows().length, 5);
  const rows = [...ui.$('task-rows').rows];
  assert.ok(rows.every(row => row.cells.length === 1));
  assert.equal(rows.find(row => row.dataset.taskId === 'ISSUE-001').querySelector('[data-complete-qa]'), null);
  assert.equal(rows.find(row => row.dataset.taskId === 'QA-001')
    .querySelector('.task-meta:last-child [data-complete-qa]').dataset.completeQa, 'QA-001');
  assert.equal(ui.$('status-filter'), null);
  assert.equal(ui.$('actionable-toggle'), null);
  assert.equal(ui.$('include-closed'), null);
  assert.equal(ui.$('navigation'), null);
  assert.equal(ui.rows().includes('DONE-001'), false);
  await ui.click('#metrics [data-queue="qa"]');
  assert.deepEqual(ui.rows(), ['QA-001', 'QA-002']);
  await ui.change('record-view', 'done');
  assert.deepEqual(ui.rows(), ['DONE-001']);
  assert.equal(ui.document.querySelector('#metrics [aria-pressed="true"]').dataset.queue, 'done');
  await ui.click('#reset-filters');
  assert.equal(ui.rows().length, 5);
  assert.equal(ui.$('record-view').value, 'all');
});

test('search, category and status filters apply without fetching product APIs', async (t) => {
  const ui = await setup(t);
  await ui.change('search', '요청');
  assert.deepEqual(ui.rows(), ['ISSUE-001']);
  await ui.click('#reset-filters');
  await ui.click('#metrics [data-queue="qa"]');
  assert.equal(ui.rows().length, 2);
  await ui.click('#metrics [data-queue="waiting"]');
  assert.deepEqual(new Set(ui.rows()),new Set(['API-001','DEC-001']));
  await ui.change('search','일치하지않음');
  assert.equal(ui.rows().length,0);
  assert.ok(ui.calls.every((call) => call.url.startsWith('/api/')));
});

test('worktree deletion explains blocks and shows confirmation and completion', async (t) => {
  const ui = await setup(t);
  const clean = {path: '/repo/clean', branch: '', head: 'abcdef0', commit: 'abcdef0123456789', backup_branch: 'tack/archive/abcdef0123456789', removable: true, uncommitted: 0, unpushed: 0, ignored_paths: ['.env.local', 'node_modules/']};
  const blocked = {...clean, path: '/repo', removable: false, remove_reasons: ['기본 워크트리입니다.']};
  const originalFetch = ui.window.fetch;
  let removed = false;
  let added = false;
  let finish;
  ui.window.fetch = async (url, options) => {
    if (url === '/api/worktrees') {
      return {ok: true, json: async () => ({worktrees: structuredClone(removed ? [blocked] : [blocked, clean])})};
    }
    if (url === '/api/worktrees/remove') {
      if (!added) {
        added = true;
        clean.ignored_paths.push('new.cache');
        return {ok: false, status: 409, json: async () => ({error: '확인하지 않은 항목입니다.'})};
      }
      assert.deepEqual(JSON.parse(options.body), {path: clean.path, commit: clean.commit, backup_branch: clean.backup_branch, ignored_paths: clean.ignored_paths});
      await new Promise(resolve => { finish = resolve; });
      removed = true;
      return {ok: true, json: async () => ({removed: true, backup_branch: clean.backup_branch})};
    }
    return originalFetch(url, options);
  };
  await ui.click('#open-worktrees');
  await ui.click('[data-remove-worktree="0"]');
  assert.match(ui.$('worktree-message').textContent, /기본 워크트리/);
  assert.equal(ui.$('worktree-confirmation').hidden, true);
  assert.match(ui.$('worktree-ignored-paths').textContent, /node_modules/);
  await ui.click('[data-remove-worktree="1"]');
  assert.equal(ui.$('worktree-confirmation').hidden, false);
  assert.match(ui.$('worktree-message').textContent, /repo\/clean/);
  assert.equal(ui.$('confirm-worktree-remove').textContent, '브랜치 보관 후 삭제');
  assert.deepEqual([...ui.$('worktree-ignored-paths').children].map(el => el.textContent), clean.ignored_paths);
  await ui.click('#cancel-worktree-remove');
  assert.equal(finish, undefined);
  await ui.click('[data-remove-worktree="1"]');
  await ui.click('#confirm-worktree-remove');
  assert.match(ui.$('worktree-message').textContent, /새로 발견된 항목 1개/);
  assert.match(ui.$('worktree-ignored-paths').textContent, /새로 발견 · new.cache/);
  await ui.click('#confirm-worktree-remove');
  assert.match(ui.$('worktree-message').textContent, /삭제 중/);
  finish();
  await new Promise(setImmediate);
  assert.match(ui.$('worktree-message').textContent, /삭제했습니다/);
  assert.ok(ui.$('worktree-message').textContent.includes(clean.backup_branch));
  assert.equal(ui.document.querySelector('[data-remove-worktree="1"]'), null);
});

test('drawer copies only the open ticket ID and reports clipboard failure', async (t) => {
  const ui = await setup(t);
  await ui.click('[data-open="ISSUE-001"]');
  await ui.click('#copy-detail-id');
  assert.deepEqual(ui.clipboard, ['ISSUE-001']);
  assert.equal(ui.$('copy-detail-status').textContent, '복사됨');
  await ui.click('#close-detail');
  await ui.click('[data-open="QA-001"]');
  assert.equal(ui.$('copy-detail-status').textContent, '');
  await ui.click('#copy-detail-id');
  assert.deepEqual(ui.clipboard, ['ISSUE-001', 'QA-001']);
  ui.window.navigator.clipboard.writeText = async () => { throw new Error('NotAllowedError'); };
  await ui.click('#copy-detail-id');
  assert.equal(ui.$('copy-detail-status').textContent, '복사 실패');
});

test('ticket evidence renders Markdown and safely opens archived HTML in the selected project', async (t) => {
  const ui = await setup(t);
  ui.records[0].sources = [
    {label: '기획안', path: 'docs/기획안.md'},
    {label: 'HTML 검토', path: 'archive:DOC-123'},
    {label: '원문', path: 'docs/generated.txt'},
    {label: '미등록 파일', path: 'private/unsupported.md'},
    {label: '관련 티켓', url: 'https://example.com/ticket'},
  ];
  const markdown = [
    '# 검토 사항', '', '**원문** <img src=x onerror=bad()> [위험 링크](javascript:bad())', '',
    '- 첫 번째', '- 두 번째', '', '| 항목 | 값 |', '| --- | --- |', '| 결과 | `통과` |', '',
    '```html', '<script>bad()</script>', '```', '', '[외부 문서](https://example.com/docs)',
  ].join('\n');
  const fetch = ui.window.fetch;
  ui.window.workUrl = path => path.includes('/source?') ? path + '&project=test-project' : path;
  ui.window.fetch = (url, options) => {
    if (!url.startsWith('/api/source?')) return fetch(url, options);
    const params = new URL(url, ui.window.location.origin).searchParams;
    assert.equal(params.get('project'), 'test-project');
    const sources = {
      'docs/기획안.md': {format: 'markdown', text: markdown},
      'archive:DOC-123': {format: 'html', text: '<script>bad()</script><h1>검토</h1>'},
      'docs/generated.txt': {format: 'text', text: '<strong>평문</strong>'},
    };
    const data = sources[params.get('path')];
    return Promise.resolve({ok: Boolean(data), json: async () => data ?? {error: '이 근거 파일은 열 수 없습니다.'}});
  };
  await ui.click('[data-open="ISSUE-001"]');
  await ui.click('[data-source-index="0"]');
  const body = ui.$('source-body');
  assert.equal(body.querySelector('h1').textContent, '검토 사항');
  assert.equal(body.querySelector('strong').textContent, '원문');
  assert.deepEqual([...body.querySelectorAll('li')].map(item => item.textContent), ['첫 번째', '두 번째']);
  assert.equal(body.querySelector('td code').textContent, '통과');
  assert.equal(body.querySelector('pre code').textContent, '<script>bad()</script>');
  assert.equal(body.querySelector('img, script, a[href^="javascript:"]'), null);
  assert.equal(body.querySelector('a').href, 'https://example.com/docs');
  assert.match(body.textContent, /<img src=x onerror=bad\(\)>/);

  await ui.click('#close-source');
  await ui.click('[data-source-index="1"]');
  const frame = body.querySelector('iframe');
  assert.equal(frame.getAttribute('sandbox'), '');
  assert.equal(frame.getAttribute('referrerpolicy'), 'no-referrer');
  assert.equal(frame.getAttribute('src'), '/source?path=archive%3ADOC-123&preview=1&project=test-project');
  assert.equal(body.querySelector('h1, script'), null);
  await ui.click('#close-source');
  assert.equal(body.querySelector('iframe'), null);
  await ui.click('[data-source-index="2"]');
  assert.equal(body.querySelector('pre').textContent, '<strong>평문</strong>');
  await ui.click('#close-source');
  await ui.click('[data-source-index="3"]');
  assert.equal(body.textContent, '이 근거 파일은 열 수 없습니다.');
  assert.equal(ui.document.querySelector('.source-list a').getAttribute('target'), '_blank');
});

test('closing one source prevents its late response from replacing the next preview', async (t) => {
  const ui = await setup(t);
  ui.records[0].sources = [
    {label: '이전 문서', path: 'docs/old.md'},
    {label: '다음 문서', path: 'docs/next.md'},
  ];
  let finish;
  const fetch = ui.window.fetch;
  ui.window.fetch = (url, options) => {
    if (url === '/api/source?path=docs%2Fold.md') return new Promise(resolve => { finish = resolve; });
    if (url === '/api/source?path=docs%2Fnext.md') return Promise.resolve({ok: true, json: async () => ({format: 'markdown', text: '# 다음 문서'})});
    return fetch(url, options);
  };
  await ui.click('[data-open="ISSUE-001"]');
  await ui.click('[data-source-index="0"]');
  assert.equal(ui.$('source-body').getAttribute('aria-busy'), 'true');
  await ui.click('#close-source');
  await ui.click('[data-source-index="1"]');
  finish({ok: true, json: async () => ({format: 'markdown', text: '# 이전 문서'})});
  await tick();
  assert.equal(ui.$('source-body').querySelector('h1').textContent, '다음 문서');
});

test('issue copy uses the current detail and exports agent context without UI or database metadata', async (t) => {
  const ui = await setup(t);
  Object.assign(ui.records[0], {
    title: '조직 조회 갱신 시 열린 이력이 닫힘',
    summary: '국가 조회 갱신 시 이력과 필터가 사라짐',
    detail: 'OrganizationManagementGrid의 history cell이 일반 열과 함께 재생성됨',
    next_action: '이력 열을 안정화하고 재조회 회귀 검증',
    acceptance: '같은 조직의 조회 갱신 시 열린 이력과 필터 유지',
    verification: '컴포넌트 재현 확인. 실제 브라우저 QA 미실행.',
    blocked_by: ['DEC-001'],
    sources: [
      { label: '조직 표', path: 'apps/ctms/src/features/inventory/organization/OrganizationManagementGrid.tsx' },
      { label: '관련 PR', url: 'https://github.com/iesg-stuff/solution_fe/pull/3734' },
    ],
    owner: 'INTERNAL_OWNER',
    version: 9,
  });
  await ui.click('[data-open="ISSUE-001"]');
  const before = structuredClone(ui.records);
  await ui.click('#detail-content [data-copy-task]');
  assert.equal(ui.clipboard.length, 1);
  const text = ui.clipboard[0];
  assert.match(text, /^# ISSUE-001 · 조직 조회 갱신 시 열린 이력이 닫힘\n/);
  for (const content of ['국가 조회 갱신 시 이력과 필터가 사라짐', 'OrganizationManagementGrid의 history cell', '이력 열을 안정화하고 재조회 회귀 검증', '같은 조직의 조회 갱신 시 열린 이력과 필터 유지', '실제 브라우저 QA 미실행', 'DEC-001 · 담당자 정책 (대기)', 'apps/ctms/src/features/inventory/organization/OrganizationManagementGrid.tsx', 'https://github.com/iesg-stuff/solution_fe/pull/3734']) {
    assert.ok(text.includes(content), content);
  }
  for (const content of ['내용·상태 수정', '에이전트용 복사', '변경 이력', '초기 등록', 'INTERNAL_OWNER', 'created_at', 'updated_at', 'version']) {
    assert.equal(text.includes(content), false, content);
  }
  assert.deepEqual(ui.records, before);
  assert.equal(ui.calls.some((call) => ['POST', 'PATCH'].includes(call.method)), false);
  assert.match(ui.$('toast').textContent, /복사했습니다/);
});

test('issue copy omits empty sections and preserves special characters as plain text', async (t) => {
  const ui = await setup(t);
  Object.assign(ui.records[0], {
    title: '<history> & 필터', summary: '  ', detail: '', next_action: '',
    acceptance: '이력 유지\n필터 유지', verification: '', last_checked: '', areas: [],
  });
  await ui.click('[data-open="ISSUE-001"]');
  await ui.click('#detail-content [data-copy-task]');
  const text = ui.clipboard[0];
  assert.match(text, /<history> & 필터/);
  assert.match(text, /이력 유지\n필터 유지/);
  for (const heading of ['문제 요약', '상세 / 현재 제약', '다음 할 일', '확인 근거 / 검증 범위', '선행 항목', '처리·결정 내용', '근거 문서·코드·티켓']) {
    assert.equal(text.includes(`## ${heading}`), false, heading);
  }
  assert.equal(text.includes('&lt;history&gt;'), false);
  assert.equal(text.includes('undefined'), false);
  assert.equal(text.includes('최근 확인일:'), false);
  await ui.click('[data-open="QA-001"]');
  assert.equal(ui.document.querySelector('#detail-content [data-copy-task]'), null);
});

test('clipboard rejection leaves the issue unchanged and allows retry', async (t) => {
  const ui = await setup(t);
  await ui.click('[data-open="ISSUE-001"]');
  const before = structuredClone(ui.records);
  ui.window.navigator.clipboard.writeText = async () => { throw new Error('NotAllowedError'); };
  await ui.click('#detail-content [data-copy-task]');
  assert.match(ui.$('toast').textContent, /복사하지 못했습니다/);
  assert.equal(ui.document.querySelector('[data-copy-task]').disabled, false);
  assert.deepEqual(ui.records, before);
  ui.window.navigator.clipboard.writeText = async (text) => { ui.clipboard.push(text); };
  await ui.click('#detail-content [data-copy-task]');
  assert.equal(ui.clipboard.length, 1);
  assert.match(ui.$('toast').textContent, /복사했습니다/);
});

test('missing clipboard API reports failure instead of claiming the text was copied', async (t) => {
  const ui = await setup(t);
  Object.defineProperty(ui.window.navigator, 'clipboard', { value: undefined });
  await ui.click('[data-open="ISSUE-001"]');
  await ui.click('#detail-content [data-copy-task]');
  assert.equal(ui.clipboard.length, 0);
  assert.match(ui.$('toast').textContent, /클립보드 권한/);
  assert.equal(ui.document.querySelector('[data-copy-task]').disabled, false);
});

test('editing persists a versioned update and refreshes detail', async (t) => {
  const ui = await setup(t);
  await ui.click('[data-open="ISSUE-001"]');
  await ui.click('[data-edit="ISSUE-001"]');
  const fields = ui.$('task-form').elements;
  fields.namedItem('queue').value = 'done';
  fields.namedItem('resolution').value = '원인 확인 후 수정';
  await ui.submit();
  const sent = ui.calls.find((call) => call.method === 'PATCH');
  assert.equal(JSON.parse(sent.body).version, 1);
  assert.equal(ui.records.find((task) => task.id === 'ISSUE-001').status, 'done');
  assert.equal(ui.$('editor').open, false);
  assert.ok(ui.$('detail-content').textContent.includes('원인 확인 후 수정'));
});

test('QA failure keeps the draft instead of marking an unexecuted test passed', async (t) => {
  const ui = await setup(t);
  await ui.click('[data-open="QA-001"]');
  await ui.click('[data-edit="QA-001"]');
  ui.$('task-form').elements.namedItem('queue').value = 'done';
  ui.$('task-form').elements.namedItem('resolution').value = '자동 테스트만 확인';
  await ui.submit();
  assert.equal(ui.$('editor').open, true);
  assert.match(ui.$('editor-error').textContent, /실제 결과/);
  assert.equal(ui.records.find((task) => task.id === 'QA-001').qa_result, 'not_run');
});

test('manual QA completion saves results and moves the record out of the remaining QA list', async (t) => {
  const ui = await setup(t);
  await ui.click('#metrics [data-queue="qa"]');
  assert.equal(ui.document.querySelector('[data-complete-qa="ISSUE-001"]'), null);
  await ui.click('[data-complete-qa="QA-001"]');
  assert.equal(ui.$('qa-completion').open, true);
  assert.equal(ui.calls.some((call) => call.method === 'PATCH'), false);
  const fields = ui.$('qa-completion-form').elements;
  assert.match(fields.namedItem('checked_at').value, /^\d{4}-\d{2}-\d{2}$/);
  fields.namedItem('actual').value = 'QA 환경: 저장 후 새로고침해도 입력값 유지';
  fields.namedItem('evidence').value = 'screenshots/qa-save.png';
  fields.namedItem('checked_at').value = '2026-10-21';
  await ui.submit('qa-completion-form');
  const payload = JSON.parse(ui.calls.find((call) => call.method === 'PATCH').body);
  assert.equal(payload.version, 1);
  assert.equal(payload.queue, 'done');
  assert.equal(payload.qa_result, 'passed');
  assert.equal(payload.last_checked, '2026-10-21');
  assert.equal(payload.qa.steps, '입력 후 재조회');
  assert.equal(payload.qa.expected, '값 유지');
  assert.equal(payload.qa.evidence, 'screenshots/qa-save.png');
  assert.match(payload.resolution, /QA 완료 \(통과\)/);
  assert.match(payload.note, /사용자 QA 완료 처리/);
  assert.equal(ui.$('qa-completion').open, false);
  assert.deepEqual(ui.rows(), ['QA-002']);
  assert.equal(ui.document.querySelector('#metrics [data-queue="qa"]').getAttribute('aria-pressed'),'true');
  assert.equal(ui.document.querySelector('#detail-content [data-complete-qa]'), null);
  assert.equal(ui.records.find((task) => task.id === 'QA-002').qa_result, 'not_run');
  await ui.click('#close-detail');
  await ui.click('#refresh');
  await ui.change('record-view', 'done');
  assert.ok(ui.rows().includes('QA-001'));
  await ui.click('[data-open="QA-001"]');
  assert.match(ui.$('detail-content').textContent, /QA 환경: 저장 후 새로고침해도 입력값 유지/);
});

test('QA detail offers completion and cancel keeps the original record unchanged', async (t) => {
  const ui = await setup(t);
  await ui.click('[data-open="QA-001"]');
  await ui.click('#detail-content [data-complete-qa="QA-001"]');
  ui.$('qa-completion-form').elements.namedItem('actual').value = '아직 저장하지 않은 결과';
  ui.window.confirm = () => false;
  await ui.click('#cancel-qa-completion');
  assert.equal(ui.$('qa-completion').open, true);
  const unload = new ui.window.Event('beforeunload', { cancelable: true });
  ui.window.dispatchEvent(unload);
  assert.equal(unload.defaultPrevented, true);
  ui.window.confirm = () => true;
  await ui.click('#close-qa-completion');
  assert.equal(ui.$('qa-completion').open, false);
  assert.equal(ui.calls.some((call) => call.method === 'PATCH'), false);
  assert.equal(ui.records.find((task) => task.id === 'QA-001').status, 'todo');
  assert.equal(ui.records.find((task) => task.id === 'QA-001').qa_result, 'not_run');
});

test('QA completion requires actual results and retains input when saving offline', async (t) => {
  const ui = await setup(t);
  await ui.click('[data-complete-qa="QA-001"]');
  const fields = ui.$('qa-completion-form').elements;
  fields.namedItem('actual').value = '  ';
  await ui.submit('qa-completion-form');
  assert.equal(ui.calls.some((call) => call.method === 'PATCH'), false);
  assert.match(ui.$('qa-completion-error').textContent, /실제 결과/);
  fields.namedItem('actual').value = '다시 저장할 확인 결과';
  fields.namedItem('evidence').value = 'screenshots/keep.png';
  ui.offline();
  await ui.submit('qa-completion-form');
  assert.equal(ui.$('qa-completion').open, true);
  assert.match(ui.$('qa-completion-error').textContent, /offline/);
  assert.equal(fields.namedItem('actual').value, '다시 저장할 확인 결과');
  assert.equal(fields.namedItem('evidence').value, 'screenshots/keep.png');
  assert.equal(ui.$('save-qa-completion').disabled, false);
  assert.equal(ui.records.find((task) => task.id === 'QA-001').status, 'todo');
});

test('QA completion conflict retains the draft without overwriting another update', async (t) => {
  const ui = await setup(t);
  await ui.click('[data-complete-qa="QA-001"]');
  ui.$('qa-completion-form').elements.namedItem('actual').value = '유지할 QA 결과';
  ui.conflict();
  await ui.submit('qa-completion-form');
  assert.equal(ui.$('qa-completion').open, true);
  assert.match(ui.$('qa-completion-error').textContent, /이미 변경/);
  assert.equal(ui.$('qa-completion-form').elements.namedItem('actual').value, '유지할 QA 결과');
  assert.equal(ui.records.find((task) => task.id === 'QA-001').qa_result, 'not_run');
});

test('not-applicable completion records the reason and preserves earlier resolution notes', async (t) => {
  const ui = await setup(t);
  ui.records.find((task) => task.id === 'QA-001').resolution = '이전 확인 내용';
  await ui.click('#refresh');
  await ui.click('[data-complete-qa="QA-001"]');
  const fields = ui.$('qa-completion-form').elements;
  fields.namedItem('result').value = 'not_applicable';
  fields.namedItem('actual').value = '사용자 확인: 해당 법인에서는 사용하지 않는 기능';
  await ui.submit('qa-completion-form');
  const saved = ui.records.find((task) => task.id === 'QA-001');
  assert.equal(saved.status, 'done');
  assert.equal(saved.qa_result, 'not_applicable');
  assert.equal(saved.qa.evidence, '');
  assert.match(saved.resolution, /^이전 확인 내용\n\nQA 완료 \(해당 없음\)/);
});

test('concurrent-write rejection retains unsaved input', async (t) => {
  const ui = await setup(t);
  await ui.click('[data-open="ISSUE-001"]');
  await ui.click('[data-edit="ISSUE-001"]');
  ui.$('task-form').elements.namedItem('summary').value = '유지할 새 입력';
  ui.conflict();
  await ui.submit();
  assert.equal(ui.$('editor').open, true);
  assert.match(ui.$('editor-error').textContent, /이미 변경/);
  assert.equal(ui.$('task-form').elements.namedItem('summary').value, '유지할 새 입력');
});

test('new records render text safely and archive is reversible', async (t) => {
  const ui = await setup(t);
  await ui.click('#add-task');
  ui.$('task-form').elements.namedItem('title').value = '<img src=x onerror=unsafe()> 확인';
  await ui.submit();
  assert.equal(ui.document.querySelector('img'), null);
  assert.match(ui.$('detail-title').textContent, /<img/);
  await ui.click('[data-archive="LOCAL-0001"]');
  assert.equal(ui.records.at(-1).status, 'archived');
  await ui.click('[data-archive="LOCAL-0001"]');
  assert.equal(ui.records.at(-1).status, 'needs_recheck');
});

test('connection failures leave existing records visible and display the error', async (t) => {
  const ui = await setup(t);
  const before = ui.rows();
  ui.offline();
  await ui.click('#refresh');
  assert.equal(ui.$('connection-error').hidden, false);
  assert.deepEqual(ui.rows(), before);
});


test('queue cards form one disjoint partition and exclude QA and issues from todo',async t=>{
  const fixture=[
    makeTask('TASK-TODO','착수할 작업','task','todo'),
    makeTask('ISSUE-RUN','조사할 문제','issue','in_progress'),
    makeTask('QA-TODO','나중에 할 QA','qa','todo'),
    makeTask('QA-RUN','수행 중인 QA','qa','in_progress'),
    makeTask('API-WAIT','계약 대기','api','todo'),
    makeTask('TASK-WAIT','결정 대기','task','waiting_decision'),
    makeTask('TASK-RUN','구현 중','task','in_progress'),
    makeTask('DONE','완료 기록','qa','done'),
    makeTask('ARCHIVED','보관 기록','issue','archived'),
  ];
  const ui=await setup(t,fixture);
  assert.equal(ui.$('category-filter'),null);
  assert.equal(ui.$('edit-category'),null);
  assert.equal(ui.$('edit-status'),null);
  assert.equal(ui.$('metrics').querySelectorAll('[data-queue]').length,6);
  for(const queue of ['todo','in_progress','issue','qa','waiting','done']) {
    await ui.change('search','일치하지않는검색어');await ui.change('priority-filter','P1');await ui.change('record-view','done');
    await ui.click(`#metrics [data-queue="${queue}"]`);
    const expected=fixture.filter(task=>queueOf(task)===queue).map(task=>task.id);
    assert.deepEqual(new Set(ui.rows()),new Set(expected));
    const card=ui.document.querySelector(`#metrics [data-queue="${queue}"]`);
    assert.equal(Number(card.querySelector('.metric-number').textContent),expected.length);
    assert.equal(ui.$('result-count').textContent,`${expected.length}건`);
    assert.equal(card.getAttribute('aria-pressed'),'true');
  }
  await ui.click('#metrics [data-queue="todo"]');assert.deepEqual(ui.rows(),['TASK-TODO']);
  await ui.change('record-view','done');assert.deepEqual(ui.rows(),['DONE']);
  await ui.change('record-view','archived');assert.deepEqual(ui.rows(),['ARCHIVED']);
});

test('project API, CLI counts, status cards and clicked rows agree', {skip: !process.env.TACK_BOARD_PARITY_FIXTURE}, async (t) => {
  const fixture = JSON.parse(fs.readFileSync(process.env.TACK_BOARD_PARITY_FIXTURE, 'utf8'));
  const ui = await setup(t, fixture.tasks);
  for (const card of [...ui.$('metrics').querySelectorAll('[data-queue]')]) {
    const queue = card.dataset.queue;
    const expected = fixture.counts[queue] || 0;
    assert.equal(Number(card.querySelector('.metric-number').textContent), expected, queue);
    await ui.click(`#metrics [data-queue="${queue}"]`);
    assert.equal(ui.rows().length, expected, queue);
    assert.equal(ui.$('result-count').textContent, `${expected}건`, queue);
    const ids = fixture.tasks.filter(task => task.queue === queue).map(task => task.id).sort();
    assert.deepEqual(ui.rows().sort(), ids, queue);
  }
});

test('full task ID search finds an archived task instead of an open task referencing its ID', async (t) => {
  const tasks = [
    makeTask('LOCAL-0353','이전 전환 작업','task','archived'),
    makeTask('LOCAL-0406','통합 전환 작업','task','todo',{detail:'LOCAL-0353의 전환 범위를 통합한다.'}),
    makeTask('LOCAL-03530','다른 ID','task'),
  ];
  const ui = await setup(t,tasks);
  await ui.click('#metrics [data-queue="todo"]');
  await ui.change('priority-filter','P1');
  await ui.change('search',' local-0353 ');
  assert.deepEqual(ui.rows(),['LOCAL-0353']);
  assert.equal(ui.$('record-view').value,'archived');
  assert.equal(ui.$('category-filter'),null);
  assert.equal(ui.$('priority-filter').value,'');
  assert.equal(ui.$('result-count').textContent,'1건');
  await ui.click('[data-open="LOCAL-0353"]');
  assert.ok(ui.$('detail-content').textContent.includes('이전 전환 작업'));
  assert.ok(ui.calls.some(call => call.url==='/api/tasks/LOCAL-0353'));
  assert.equal(ui.calls.some(call => ['POST','PATCH'].includes(call.method)),false);
});

test('ID search handles completed and missing IDs while free text still searches task content', async (t) => {
  const ui = await setup(t,[
    makeTask('LOCAL-0353','완료된 작업','task','done',{resolution:'검증 완료'}),
    makeTask('LOCAL-0406','진행 작업','task','todo',{detail:'통합 대상 LOCAL-9999 및 사용자 입력 계약'}),
  ]);
  await ui.change('search','LOCAL-0353');
  assert.deepEqual(ui.rows(),['LOCAL-0353']);
  assert.equal(ui.$('record-view').value,'done');
  await ui.change('search','LOCAL-0406');
  assert.deepEqual(ui.rows(),['LOCAL-0406']);
  assert.equal(ui.$('record-view').value,'all');
  await ui.change('search','LOCAL-040');
  assert.deepEqual(ui.rows(),['LOCAL-0406']);
  await ui.change('search','LOCAL-9999');
  assert.deepEqual(ui.rows(),[]);
  await ui.change('search','사용자 입력');
  assert.deepEqual(ui.rows(),['LOCAL-0406']);
});


test('subtasks are created with the selected parent and can navigate back to it',async t=>{
  const ui=await setup(t);
  await ui.click('[data-open="ISSUE-001"]');
  await ui.click('[data-add-subtask]');
  assert.equal(ui.$('task-form').elements.parent_id.value,'ISSUE-001');
  assert.equal(ui.$('task-form').elements.queue.value,'todo');
  ui.$('task-form').elements.title.value='독립 FE 구현';
  await ui.submit();
  const child=ui.records.find(task=>task.title==='독립 FE 구현');
  assert.equal(child.parent_id,'ISSUE-001');
  assert.ok(ui.$('detail-content').textContent.includes('상위 작업'));
  await ui.click('#detail-content [data-open="ISSUE-001"]');
  assert.ok(ui.$('detail-content').textContent.includes('하위 작업'));
});

test('replacement links show inverse navigation, copy context, and versioned removal',async t=>{
  const ui=await setup(t);
  await ui.click('[data-open="ISSUE-001"]');
  await ui.change('relation-type','superseded_by');
  await ui.change('relation-target','DEC-001');
  await ui.click('#relation-add');
  assert.ok(ui.$('detail-content').textContent.includes('통합·대체 대상'));
  await ui.click('#detail-content [data-copy-task]');
  assert.match(ui.clipboard[0],/통합·대체 대상 · DEC-001/);
  await ui.click('#detail-content [data-open="DEC-001"]');
  assert.ok(ui.$('detail-content').textContent.includes('통합·대체한 작업'));
  await ui.click('#detail-content [data-open="ISSUE-001"]');
  await ui.click('[data-unlink="superseded_by"]');
  assert.equal(ui.records[0].relations.length,0);
  assert.equal(ui.records[0].version,3);
});

test('relation version conflict preserves the entered target and reports the failure',async t=>{
  const ui=await setup(t);await ui.click('[data-open="ISSUE-001"]');
  await ui.change('relation-type','relates_to');await ui.change('relation-target','DEC-001');ui.conflict();
  await ui.click('#relation-add');
  assert.equal(ui.$('relation-target').value,'DEC-001');
  assert.equal(ui.$('relation-error').hidden,false);
  assert.equal(ui.records[0].relations.length,0);
});

test('all-records view includes open, done and archived tasks with common filters',async t=>{
  const ui=await setup(t,[
    makeTask('LOCAL-0001','공통 진행','task','todo'),
    makeTask('LOCAL-0002','공통 완료','decision','done',{resolution:'검증'}),
    makeTask('LOCAL-0003','공통 보관','decision','archived'),
  ]);
  assert.deepEqual(ui.rows(),['LOCAL-0001']);
  await ui.change('record-view','everything');
  assert.deepEqual(new Set(ui.rows()),new Set(['LOCAL-0001','LOCAL-0002','LOCAL-0003']));
  assert.equal(ui.$('list-title').textContent,'전체 목록');
  assert.equal(ui.$('result-count').textContent,'3건');
  await ui.change('area-filter','Scope 1');
  assert.equal(ui.rows().length,3);
  await ui.change('search','공통');
  assert.equal(ui.rows().length,3);
  await ui.change('search','LOCAL-0003');
  assert.deepEqual(ui.rows(),['LOCAL-0003']);
  assert.equal(ui.$('record-view').value,'everything');
  await ui.change('search','');await ui.click('#refresh');
  assert.equal(ui.rows().length,3);
  assert.equal(ui.$('record-view').value,'everything');
  await ui.click('#metrics [data-queue="todo"]');
  assert.deepEqual(ui.rows(),['LOCAL-0001']);
  assert.equal(ui.$('record-view').value,'all');
  assert.equal(ui.$('list-title').textContent,'할 일');
});


test('API-only filter works across queues and closed records without restoring a classification axis',async t=>{
  const ui=await setup(t,[
    makeTask('API-001','API 계약 요청','api','waiting_api'),
    makeTask('LOCAL-0002','진행할 API 요청','task','todo',{is_api_request:true}),
    makeTask('LOCAL-0003','완료한 API 요청','task','done',{is_api_request:true,resolution:'검증'}),
    makeTask('LOCAL-0004','일반 작업','task','todo'),
  ]);
  ui.$('api-only').checked=true;await ui.change('api-only',true);
  assert.deepEqual(new Set(ui.rows()),new Set(['API-001','LOCAL-0002']));
  assert.match(ui.$('list-title').textContent,/API 요청/);
  assert.equal(ui.document.querySelector('#metrics [data-queue="todo"] .metric-number').textContent,'1');
  assert.equal(ui.document.querySelector('#metrics [data-queue="waiting"] .metric-number').textContent,'1');
  await ui.click('#metrics [data-queue="todo"]');
  assert.deepEqual(ui.rows(),['LOCAL-0002']);assert.equal(ui.$('api-only').checked,true);
  await ui.change('record-view','everything');
  assert.deepEqual(new Set(ui.rows()),new Set(['API-001','LOCAL-0002','LOCAL-0003']));
  await ui.change('search','LOCAL-0004');
  assert.deepEqual(ui.rows(),['LOCAL-0004']);assert.equal(ui.$('api-only').checked,false);
  await ui.click('#reset-filters');assert.equal(ui.$('api-only').checked,false);
});

test('API request marker is edited separately and survives a queue change',async t=>{
  const ui=await setup(t,[makeTask('API-001','요청','api','waiting_api')]);
  await ui.click('[data-open="API-001"]');await ui.click('[data-edit="API-001"]');
  const fields=ui.$('task-form').elements;
  assert.equal(fields.is_api_request.checked,true);
  fields.queue.value='todo';await ui.submit();
  assert.equal(ui.records[0].queue,'todo');assert.equal(ui.records[0].is_api_request,true);
  ui.$('api-only').checked=true;await ui.change('api-only',true);
  assert.deepEqual(ui.rows(),['API-001']);
  await ui.click('#add-task');
  assert.equal(ui.$('task-form').elements.is_api_request.checked,true);
  ui.$('task-form').elements.title.value='새 API 요청';await ui.submit();
  const created=ui.records.find(task=>task.title==='새 API 요청');assert.equal(created.is_api_request,true);
  await ui.click(`[data-edit="${created.id}"]`);
  ui.$('task-form').elements.is_api_request.checked=false;await ui.submit();
  assert.equal(ui.records.find(task=>task.id===created.id).is_api_request,false);
  assert.deepEqual(ui.rows(),['API-001']);
});


test('page route is visible, searchable, copied and saved by the issue editor',async t=>{
  const route='/ctms/workspaces/[id]/scope1?org-item-id=104&tab=history';
  const ui=await setup(t,[makeTask('ISSUE-001','목록 이력 유지','issue','todo',{page_path:route})]);
  assert.equal(ui.document.querySelector('.task-page-path').textContent,route);
  await ui.change('search','/ctms/workspaces');assert.deepEqual(ui.rows(),['ISSUE-001']);
  await ui.click('[data-open="ISSUE-001"]');
  assert.equal(ui.document.querySelector('.detail-page-path code').textContent,route);
  await ui.click('[data-copy-task]');assert.ok(ui.clipboard[0].includes('## 페이지 경로\n'+route));
  await ui.click('[data-edit="ISSUE-001"]');
  assert.equal(ui.$('task-form').elements.page_path.value,route);
  ui.$('task-form').elements.page_path.value='/ctms/workspaces/3/workplaces';await ui.submit();
  const sent=JSON.parse(ui.calls.find(call=>call.method==='PATCH').body);
  assert.equal(sent.page_path,'/ctms/workspaces/3/workplaces');
  assert.equal(ui.document.querySelector('.detail-page-path code').textContent,sent.page_path);
});

test('page routes are escaped as text and legacy issues can add a route',async t=>{
  const unsafe='/ctms/<svg onload="alert(1)">?tab=a&value=b';
  const ui=await setup(t,[makeTask('ISSUE-001','기존 작업','issue','todo',{page_path:unsafe})]);
  assert.equal(ui.document.querySelector('.task-page-path svg'),null);
  await ui.click('[data-open="ISSUE-001"]');
  assert.equal(ui.document.querySelector('.detail-page-path code').textContent,unsafe);
  assert.equal(ui.document.querySelector('.detail-page-path svg'),null);
});
