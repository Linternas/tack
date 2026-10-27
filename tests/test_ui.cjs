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
const makeTask = (id, title, category, status = 'todo', options = {}) => ({
  id, title, category, status, priority: 'P2', owner: 'FE', areas: ['Scope 1'],
  summary: '확인할 배경', detail: '확인한 계약', next_action: '다음 확인', acceptance: '기대하는 완료',
  verification: '코드', last_checked: '2026-10-22', resolution: '', sources: [], blocked_by: [],
  qa_result: 'not_run', qa: { preconditions: '테스트 법인 준비', steps: '입력 후 재조회', expected: '값 유지', actual: '', evidence: '' },
  version: 1, created_at: '2026-10-22T00:00:00Z', updated_at: '2026-10-22T00:00:00Z', ...options,
});

async function setup(context) {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8788/', runScripts: 'outside-only' });
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
  const records = [
    makeTask('ISSUE-001', '목록 요청 버튼', 'issue', 'needs_recheck', { priority: 'P1' }),
    makeTask('DEC-001', '담당자 정책', 'decision', 'waiting_decision'),
    makeTask('API-001', '계산 응답', 'api', 'waiting_api'),
    makeTask('QA-001', '입력 저장 QA', 'qa'),
    makeTask('QA-002', '선행 결정 필요한 QA', 'qa', 'todo', { blocked_by: ['DEC-001'] }),
    makeTask('DONE-001', '처리한 문제', 'issue', 'done', { resolution: '수정·자동 검증' }),
  ];
  const calls = [];
  let forceConflict = false;
  let offline = false;
  window.fetch = async (url, options = {}) => {
    calls.push({ url, ...options });
    if (offline) throw new Error('offline');
    let status = 200;
    let data;
    const method = options.method || 'GET';
    if (url === '/api/tasks' && method === 'GET') data = { tasks: structuredClone(records), saved_at: '2026-10-22T01:00:00Z' };
    else if (url.startsWith('/api/source')) data = { text: '<script>unsafe()</script> 원문' };
    else if (method === 'PATCH' || method === 'POST') {
      const payload = JSON.parse(options.body);
      const id = url.split('/').at(-1);
      const index = records.findIndex((task) => task.id === id);
      const candidate = index >= 0 ? { ...records[index], ...payload } : payload;
      if (forceConflict) { status = 409; data = { error: '다른 창에서 이미 변경됐습니다.' }; }
      else if (candidate.category === 'qa' && candidate.status === 'done' && (!['passed', 'not_applicable'].includes(candidate.qa_result) || !candidate.qa.actual.trim())) { status = 400; data = { error: 'QA 완료에는 통과 또는 해당 없음 판정과 실제 결과가 필요합니다.' }; }
      else {
        const task = index >= 0 ? { ...records[index], ...payload, version: records[index].version + 1 } : makeTask('LOCAL-0001', payload.title, payload.category, payload.status, payload);
        if (index >= 0) records[index] = task; else records.push(task);
        data = { task: structuredClone(task), history: [] };
      }
    } else {
      const task = records.find((item) => item.id === url.split('/').at(-1));
      data = { task: structuredClone(task), history: [{ action: 'created', note: '초기 등록', created_at: task.created_at }] };
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

test('initial view separates open tasks, completed history and actionable dependencies', async (t) => {
  const ui = await setup(t);
  assert.equal(ui.rows().length, 5);
  assert.equal(ui.$('actionable-count').textContent, '2건');
  assert.equal(ui.rows().includes('DONE-001'), false);
  await ui.click('#actionable-toggle');
  assert.deepEqual(ui.rows(), ['ISSUE-001', 'QA-001']);
  await ui.click('[data-mode="done"]');
  assert.deepEqual(ui.rows(), ['DONE-001']);
});

test('search, category and status filters apply without fetching product APIs', async (t) => {
  const ui = await setup(t);
  await ui.change('search', '요청');
  assert.deepEqual(ui.rows(), ['ISSUE-001']);
  await ui.click('#reset-filters');
  await ui.click('[data-mode="qa"]');
  assert.equal(ui.rows().length, 2);
  await ui.change('status-filter', 'waiting_api');
  assert.equal(ui.rows().length, 0);
  assert.ok(ui.calls.every((call) => call.url.startsWith('/api/')));
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
  for (const content of ['국가 조회 갱신 시 이력과 필터가 사라짐', 'OrganizationManagementGrid의 history cell', '이력 열을 안정화하고 재조회 회귀 검증', '같은 조직의 조회 갱신 시 열린 이력과 필터 유지', '실제 브라우저 QA 미실행', 'DEC-001 · 담당자 정책 (기획 결정 대기)', 'apps/ctms/src/features/inventory/organization/OrganizationManagementGrid.tsx', 'https://github.com/iesg-stuff/solution_fe/pull/3734']) {
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
  fields.namedItem('status').value = 'done';
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
  ui.$('task-form').elements.namedItem('status').value = 'done';
  ui.$('task-form').elements.namedItem('resolution').value = '자동 테스트만 확인';
  await ui.submit();
  assert.equal(ui.$('editor').open, true);
  assert.match(ui.$('editor-error').textContent, /실제 결과/);
  assert.equal(ui.records.find((task) => task.id === 'QA-001').qa_result, 'not_run');
});

test('manual QA completion saves results and moves the record out of the remaining QA list', async (t) => {
  const ui = await setup(t);
  await ui.click('[data-mode="qa"]');
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
  assert.equal(payload.status, 'done');
  assert.equal(payload.qa_result, 'passed');
  assert.equal(payload.last_checked, '2026-10-21');
  assert.equal(payload.qa.steps, '입력 후 재조회');
  assert.equal(payload.qa.expected, '값 유지');
  assert.equal(payload.qa.evidence, 'screenshots/qa-save.png');
  assert.match(payload.resolution, /QA 완료 \(통과\)/);
  assert.match(payload.note, /사용자 QA 완료 처리/);
  assert.equal(ui.$('qa-completion').open, false);
  assert.deepEqual(ui.rows(), ['QA-002']);
  assert.equal(ui.document.querySelector('.metric.qa .metric-number').textContent, '1');
  assert.equal(ui.document.querySelector('#detail-content [data-complete-qa]'), null);
  assert.equal(ui.records.find((task) => task.id === 'QA-002').qa_result, 'not_run');
  await ui.click('#close-detail');
  await ui.click('#refresh');
  await ui.click('[data-mode="done"]');
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
