const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const { createRequire } = Module;
const originalLoad = Module._load;
Module._load = function (name, ...args) { return name === 'canvas' ? {} : originalLoad.call(this, name, ...args); };
const { JSDOM } = createRequire(require.resolve('jest-environment-jsdom', { paths: [process.env.AGENT_WORK_JSDOM_ROOT || process.cwd()] }))('jsdom');
Module._load = originalLoad;
const html = fs.readFileSync(path.join(__dirname, '../agent_work/assets/qa.html'), 'utf8');
const script = fs.readFileSync(path.join(__dirname, '../agent_work/assets/qa.js'), 'utf8');
const tick = async () => { await new Promise(setImmediate); await new Promise(setImmediate); };
const makeTask = (id, title, category = 'qa', overrides = {}) => ({
  id, title, category, status: 'todo', priority: 'P2', owner: 'QA', areas: ['조직'],
  summary: '저장 동작 확인', detail: '테스트 데이터만 사용', next_action: '수동 확인', acceptance: '입력값 유지',
  verification: '코드만 확인. 수동 QA 미실행.', last_checked: '', resolution: '', sources: [], blocked_by: [],
  qa_result: 'not_run', qa: { preconditions: 'QA 환경에서 조직 화면 진입', steps: '1. 행을 연다.\n2. 값을 저장하고 다시 조회한다.', expected: '입력값 유지', actual: '', evidence: '' },
  version: 1, created_at: '2026-10-23T00:00:00Z', updated_at: '2026-10-23T00:00:00Z', ...overrides,
});
async function setup(t, hash = '') {
  const dom = new JSDOM(html, { url: `http://127.0.0.1:8788/qa${hash}`, runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  const { window } = dom;
  window.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  window.HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
  window.confirm = () => true;
  const records = [
    makeTask('QA-001', '저장 후 재조회'),
    makeTask('QA-002', '선행 확인 필요', 'qa', { status: 'blocked', blocked_by: ['DEC-001'] }),
    makeTask('QA-003', '확인한 시나리오', 'qa', { status: 'done', qa_result: 'passed', resolution: '사용자 확인', qa: { preconditions: '기존 준비', steps: '기존 절차', expected: '기존 기대', actual: '기존 실행 결과', evidence: 'old.png' } }),
    makeTask('DEC-001', '기획 결정', 'decision', { status: 'waiting_decision' }),
    makeTask('ISSUE-001', '등록된 제품 문제', 'issue'),
  ];
  const calls = [];
  const histories = new Map();
  const gates = new Map();
  let conflict = false;
  let offline = false;
  let number = 0;
  window.fetch = async (url, options = {}) => {
    const method = options.method || 'GET';
    calls.push({ url, method, ...options });
    if (gates.has(`${method} ${url}`)) await gates.get(`${method} ${url}`);
    if (offline) throw new Error('offline');
    let status = 200;
    let data;
    if (method === 'GET' && url === '/api/tasks') data = { tasks: structuredClone(records) };
    else {
      const id = url.split('/').at(-1);
      const index = records.findIndex((task) => task.id === id);
      if (method === 'GET') data = { task: structuredClone(records[index]), history: histories.get(id) || [] };
      else {
        const payload = JSON.parse(options.body);
        if (conflict || (method === 'PATCH' && payload.version !== records[index].version)) {
          status = 409; data = { error: '다른 창에서 이미 변경됐습니다. 입력을 보관한 뒤 최신 내용을 다시 열어주세요.' };
        } else {
          const task = method === 'PATCH'
            ? { ...records[index], ...payload, version: records[index].version + 1 }
            : makeTask(`LOCAL-${`${++number}`.padStart(4, '0')}`, payload.title, payload.category, payload);
          if (task.status === 'done' && (!['passed', 'not_applicable'].includes(task.qa_result) || !task.qa.actual.trim() || !task.resolution.trim())) {
            status = 400; data = { error: '완료에는 실제 결과와 통과 판정이 필요합니다.' };
          } else {
            if (method === 'PATCH') records[index] = task; else records.push(task);
            const history = [{ created_at: task.updated_at, note: payload.note, action: method === 'PATCH' ? 'updated' : 'created' }, ...(histories.get(task.id) || [])];
            histories.set(task.id, history);
            data = { task: structuredClone(task), history: structuredClone(history) };
          }
        }
      }
    }
    return { ok: status < 400, status, json: async () => data };
  };
  window.eval(script);
  await tick();
  const document = window.document;
  const $ = (id) => document.getElementById(id);
  const click = async (selector) => { document.querySelector(selector).click(); await tick(); };
  const change = async (id, value) => { $(id).value = value; $(id).dispatchEvent(new window.Event(id === 'search' ? 'input' : 'change')); await tick(); };
  const submit = async (id, next = false) => {
    const event = new window.Event('submit', { bubbles: true, cancelable: true });
    if (id === 'result-form') Object.defineProperty(event, 'submitter', { value: $(next ? 'save-next' : 'save-result') });
    $(id).dispatchEvent(event);
    await tick();
  };
  const delay = (key) => { let release; const promise = new Promise((resolve) => { release = resolve; }); gates.set(key, promise); return () => { gates.delete(key); release(); }; };
  return { window, document, $, click, change, submit, records, calls, delay, conflict: (value = true) => { conflict = value; }, offline: (value = true) => { offline = value; } };
}
function fillScenario(ui, title = '새 시나리오') {
  const fields = ui.$('scenario-form').elements;
  for (const [name, value] of Object.entries({ title, areas: '조직, Scope 1', preconditions: '테스트 조직에서 시작', steps: '목록에 진입한다.\n값을 저장한다.', expected: '저장한 값 유지' })) fields.namedItem(name).value = value;
}

test('existing scenarios expose prerequisites, ordered steps, expectations and dependency warnings', async (t) => {
  const ui = await setup(t);
  assert.equal(ui.$('total-count').textContent, '3');
  assert.equal(ui.$('remaining-count').textContent, '2');
  assert.equal(ui.$('completed-count').textContent, '1');
  assert.equal(ui.document.querySelectorAll('[data-scenario]').length, 2);
  assert.equal(ui.document.querySelectorAll('.qa-step-list li').length, 2);
  assert.match(ui.$('scenario-content').textContent, /QA 환경에서 조직 화면 진입/);
  assert.match(ui.$('scenario-content').textContent, /입력값 유지/);
  assert.equal(ui.$('qa-result').value, 'not_run');
  assert.equal(ui.calls.some((call) => call.method !== 'GET'), false);
  await ui.click('[data-scenario="QA-002"]');
  assert.match(ui.document.querySelector('.qa-blocker').textContent, /기획 결정 대기/);
  await ui.submit('result-form');
  assert.equal(ui.records[1].status, 'blocked');
  assert.ok(ui.calls.every((call) => call.url.startsWith('/api/tasks')));
});

test('scenario creation persists instructions and escapes titles without marking QA complete', async (t) => {
  const ui = await setup(t);
  await ui.click('#add-scenario');
  fillScenario(ui, '<img src=x onerror=unsafe()> 새 테스트');
  await ui.submit('scenario-form');
  const saved = ui.records.find((task) => task.id === 'LOCAL-0001');
  assert.equal(saved.category, 'qa');
  assert.equal(saved.status, 'todo');
  assert.equal(saved.qa_result, 'not_run');
  assert.equal(saved.qa.actual, '');
  assert.equal(saved.qa.steps, '목록에 진입한다.\n값을 저장한다.');
  assert.equal(ui.$('scenario-editor').open, false);
  assert.equal(ui.document.querySelector('img'), null);
  assert.match(ui.$('scenario-content').textContent, /<img/);
  assert.equal(ui.window.location.hash, '#LOCAL-0001');
  await ui.click('#refresh');
  assert.match(ui.$('scenario-content').textContent, /<img/);
});

test('editing instructions preserves execution results, evidence, sources and completion status', async (t) => {
  const ui = await setup(t, '#QA-003');
  ui.records[2].sources = [{ label: '기존 문서', path: 'apps/ctms/src/example.ts' }];
  await ui.click('#refresh');
  await ui.click('#edit-scenario');
  ui.$('scenario-form').elements.namedItem('steps').value = '바뀐 절차';
  await ui.submit('scenario-form');
  assert.equal(ui.records[2].qa.steps, '바뀐 절차');
  assert.equal(ui.records[2].qa.actual, '기존 실행 결과');
  assert.equal(ui.records[2].qa.evidence, 'old.png');
  assert.equal(ui.records[2].qa_result, 'passed');
  assert.equal(ui.records[2].status, 'done');
  assert.equal(ui.records[2].sources[0].path, 'apps/ctms/src/example.ts');
});

test('deleting a scenario hides it while preserving instructions and execution history for restore', async (t) => {
  const ui = await setup(t, '#QA-003');
  await ui.click('#delete-scenario');
  assert.equal(ui.records[2].status, 'archived');
  assert.equal(ui.records[2].qa.actual, '기존 실행 결과');
  assert.equal(ui.$('result-fields').disabled, true);
  await ui.change('result-filter', 'archived');
  assert.ok(ui.document.querySelector('[data-scenario="QA-003"]'));
  await ui.click('#restore-scenario');
  assert.equal(ui.records[2].status, 'needs_recheck');
  assert.equal(ui.records[2].qa.actual, '기존 실행 결과');
  assert.equal(ui.calls.some((call) => call.method === 'DELETE'), false);
  await ui.change('result-filter', 'all');
  assert.ok(ui.document.querySelector('[data-scenario="QA-003"]'));
});

test('failure recording requires a brief actual result and survives refresh without creating an issue automatically', async (t) => {
  const ui = await setup(t);
  await ui.change('qa-result', 'failed');
  await ui.submit('result-form');
  assert.match(ui.$('result-error').textContent, /짧게 기록/);
  assert.equal(ui.calls.some((call) => call.method === 'PATCH'), false);
  ui.$('result-form').elements.namedItem('actual').value = '2단계: 저장 후 새로고침하면 값이 사라짐';
  await ui.submit('result-form');
  assert.equal(ui.records[0].status, 'needs_recheck');
  assert.equal(ui.records[0].qa_result, 'failed');
  assert.equal(ui.records[0].qa.preconditions, 'QA 환경에서 조직 화면 진입');
  assert.equal(ui.records.filter((task) => task.category === 'issue').length, 1);
  assert.equal(ui.$('failed-count').textContent, '1');
  await ui.click('#refresh');
  assert.equal(ui.$('result-form').elements.namedItem('actual').value, '2단계: 저장 후 새로고침하면 값이 사라짐');
  assert.match(ui.document.querySelector('.qa-history').textContent, /저장 후 새로고침하면 값이 사라짐/);
});

test('passing saves an explicit manual result and advances to the next scenario', async (t) => {
  const ui = await setup(t);
  await ui.change('qa-result', 'passed');
  ui.$('result-form').elements.namedItem('actual').value = '직접 확인: 재조회 후 값 유지';
  await ui.submit('result-form', true);
  assert.equal(ui.records[0].status, 'done');
  assert.match(ui.records[0].resolution, /직접 확인/);
  assert.equal(ui.$('completed-count').textContent, '2');
  assert.equal(ui.window.location.hash, '#QA-002');
  assert.equal(ui.$('qa-result').value, 'not_run');
  assert.equal(ui.records[1].status, 'blocked');
});

test('not-applicable QA records the reason and keeps the date chosen by the user', async (t) => {
  const ui = await setup(t);
  await ui.change('qa-result', 'not_applicable');
  const fields = ui.$('result-form').elements;
  fields.namedItem('actual').value = '이 환경에서 사용하지 않는 기능';
  fields.namedItem('checked_at').value = '2026-10-22';
  await ui.submit('result-form');
  assert.equal(ui.records[0].status, 'done');
  assert.equal(ui.records[0].last_checked, '2026-10-22');
  assert.equal(ui.$('result-form').elements.namedItem('checked_at').value, '2026-10-22');
});

test('unsaved results survive cancelled navigation and warn when leaving the page', async (t) => {
  const ui = await setup(t);
  ui.$('result-form').elements.namedItem('actual').value = '아직 저장하지 않은 문제';
  ui.window.confirm = () => false;
  await ui.click('[data-scenario="QA-002"]');
  assert.equal(ui.window.location.hash, '#QA-001');
  assert.equal(ui.$('result-form').elements.namedItem('actual').value, '아직 저장하지 않은 문제');
  await ui.click('#refresh');
  assert.equal(ui.$('result-form').elements.namedItem('actual').value, '아직 저장하지 않은 문제');
  const event = new ui.window.Event('beforeunload', { cancelable: true });
  ui.window.dispatchEvent(event);
  assert.equal(event.defaultPrevented, true);
});

test('version conflicts and offline saves retain drafts and allow a later retry', async (t) => {
  const ui = await setup(t);
  await ui.change('qa-result', 'failed');
  ui.$('result-form').elements.namedItem('actual').value = '충돌 시 보존할 문제';
  ui.conflict();
  await ui.submit('result-form');
  assert.match(ui.$('result-error').textContent, /이미 변경/);
  assert.equal(ui.$('result-form').elements.namedItem('actual').value, '충돌 시 보존할 문제');
  assert.equal(ui.$('result-fields').disabled, false);
  assert.equal(ui.records[0].version, 1);
  ui.conflict(false);
  ui.offline();
  await ui.submit('result-form');
  assert.match(ui.$('result-error').textContent, /offline/);
  ui.offline(false);
  await ui.submit('result-form');
  assert.equal(ui.records[0].qa_result, 'failed');
});

test('a pending save disables edits and prevents duplicate mutations', async (t) => {
  const ui = await setup(t);
  await ui.change('qa-result', 'partial');
  ui.$('result-form').elements.namedItem('actual').value = '1단계까지만 확인';
  const release = ui.delay('PATCH /api/tasks/QA-001');
  await ui.submit('result-form');
  assert.equal(ui.$('result-fields').disabled, true);
  assert.equal(ui.$('add-scenario').disabled, true);
  await ui.submit('result-form');
  assert.equal(ui.calls.filter((call) => call.method === 'PATCH').length, 1);
  release();
  await tick();
  assert.equal(ui.records[0].status, 'in_progress');
  assert.equal(ui.$('result-fields').disabled, false);
});

test('a delayed detail read cannot replace a newly saved scenario', async (t) => {
  const ui = await setup(t);
  const release = ui.delay('GET /api/tasks/QA-002');
  await ui.click('[data-scenario="QA-002"]');
  await ui.click('#add-scenario');
  fillScenario(ui);
  await ui.submit('scenario-form');
  assert.equal(ui.window.location.hash, '#LOCAL-0001');
  release();
  await tick();
  assert.equal(ui.window.location.hash, '#LOCAL-0001');
  assert.match(ui.$('scenario-content').textContent, /새 시나리오/);
});

test('scenario editor failures and cancel keep stored instructions unchanged', async (t) => {
  const ui = await setup(t);
  await ui.click('#edit-scenario');
  ui.$('scenario-form').elements.namedItem('steps').value = '입력한 새 절차';
  ui.offline();
  await ui.submit('scenario-form');
  assert.equal(ui.$('scenario-editor').open, true);
  assert.equal(ui.$('scenario-form').elements.namedItem('steps').value, '입력한 새 절차');
  assert.match(ui.$('editor-error').textContent, /offline/);
  ui.window.confirm = () => false;
  await ui.click('#cancel-editor');
  assert.equal(ui.$('scenario-editor').open, true);
  ui.window.confirm = () => true;
  await ui.click('#cancel-editor');
  assert.equal(ui.$('scenario-editor').open, false);
  assert.equal(ui.records[0].qa.steps, '1. 행을 연다.\n2. 값을 저장하고 다시 조회한다.');
});
