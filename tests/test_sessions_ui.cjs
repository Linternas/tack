const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const originalLoad = Module._load;
Module._load = function (name, ...args) { return name === 'canvas' ? {} : originalLoad.call(this, name, ...args); };
const { JSDOM } = Module.createRequire(require.resolve('jest-environment-jsdom', { paths: [process.env.AGENT_WORK_JSDOM_ROOT || process.cwd()] }))('jsdom');
Module._load = originalLoad;
const asset = (name) => fs.readFileSync(path.join(__dirname, '../agent_work/assets', name), 'utf8');
const tick = async () => { await new Promise(setImmediate); await new Promise(setImmediate); };

test('project context stays on task APIs, QA links, exports and session navigation', async (t) => {
  const dom = new JSDOM(asset('index.html'), { url: 'http://127.0.0.1:8788/?project=project-b', runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  dom.window.fetch = async () => ({ ok: true, json: async () => ({ project: { id: 'project-b', name: '<b>Project B</b>' }, projects: [{ id: 'project-b', name: '<b>Project B</b>' }] }) });
  dom.window.eval(asset('common.js'));
  await tick();
  assert.equal(dom.window.workUrl('/api/tasks/LOCAL-0001'), '/api/tasks/LOCAL-0001?project=project-b');
  assert.equal(dom.window.workUrl('/api/source?path=docs%2Ftest.md'), '/api/source?path=docs%2Ftest.md&project=project-b');
  assert.equal(dom.window.document.querySelector('a[href^="/qa"]').getAttribute('href'), '/qa?project=project-b');
  assert.equal(dom.window.document.querySelector('a[download]').getAttribute('href'), '/api/export?project=project-b');
  assert.equal(dom.window.document.getElementById('project-picker').textContent, '<b>Project B</b>');
  assert.equal(dom.window.document.getElementById('project-picker').querySelector('b'), null);
  assert.equal(dom.window.document.getElementById('current-project').textContent, '<b>Project B</b>');
  assert.equal(dom.window.document.getElementById('current-project').querySelector('b'), null);
  assert.equal(dom.window.document.title, '<b>Project B</b> · Tack');
});

test('session conflicts retain the draft; successful checkpoint reloads version and history', async (t) => {
  const dom = new JSDOM(asset('sessions.html'), { url: 'http://127.0.0.1:8788/sessions', runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  const w = dom.window;
  w.workUrl = (p) => p;
  w.confirm = () => false;
  let conflict = true;
  const session = { id: 'RUN-ABC', task_id: 'LOCAL-0001', version: 1, goal: '<script>unsafe()</script>', status: 'active', next_action: '다음', decisions: '', changed_files: '', verification: '', blockers: '', branch: 'feature/test' };
  const calls = [];
  w.fetch = async (url, options = {}) => {
    calls.push([url, options]);
    let data;
    if (url === '/api/sessions') data = { sessions: [session] };
    else if (options.method === 'PATCH') {
      if (conflict) return { ok: false, json: async () => ({ error: '다른 세션에서 변경됨' }) };
      Object.assign(session, JSON.parse(options.body), { version: 2 });
      data = { session };
    } else data = { session, checkpoints: [{ data: JSON.stringify(session), created_at: '2026-10-27' }] };
    return { ok: true, json: async () => structuredClone(data) };
  };
  w.eval(asset('sessions.js'));
  await tick();
  assert.equal(w.document.querySelector('#session-list script'), null);
  w.document.querySelector('#session-list button').click();
  await tick();
  const form = w.document.getElementById('checkpoint-form');
  form.elements.next_action.value = '입력 보존';
  form.dispatchEvent(new w.Event('submit', { cancelable: true }));
  await tick();
  assert.equal(form.elements.next_action.value, '입력 보존');
  assert.match(w.document.getElementById('session-error').textContent, /변경됨/);
  assert.equal(JSON.parse(calls.find(([, o]) => o.method === 'PATCH')[1].body).version, 1);
  conflict = false;
  form.dispatchEvent(new w.Event('submit', { cancelable: true }));
  await tick();
  assert.equal(session.version, 2);
  assert.equal(w.document.querySelector('#checkpoint-history pre').textContent.includes('입력 보존'), true);
  assert.equal(w.document.getElementById('session-error').textContent, '저장 완료');
});

test('delayed session reads cannot replace the session selected later', async (t) => {
  const dom = new JSDOM(asset('sessions.html'), { url: 'http://127.0.0.1:8788/sessions', runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  const w = dom.window;
  w.workUrl = (p) => p;
  w.confirm = () => true;
  const make = (id) => ({ id, task_id: 'LOCAL-0001', version: 1, goal: id, status: 'active', next_action: 'next', decisions: '', changed_files: '', verification: '', blockers: '' });
  const first = make('RUN-FIRST');
  const second = make('RUN-SECOND');
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  w.fetch = async (url) => {
    if (url === '/api/sessions') return { ok: true, json: async () => ({ sessions: [first, second] }) };
    if (url.endsWith(first.id)) await gate;
    return { ok: true, json: async () => ({ session: url.endsWith(first.id) ? first : second, checkpoints: [] }) };
  };
  w.eval(asset('sessions.js'));
  await tick();
  const buttons = w.document.querySelectorAll('#session-list button');
  buttons[0].click();
  buttons[1].click();
  await tick();
  assert.equal(w.document.getElementById('checkpoint-form').elements.goal.value, second.id);
  release();
  await tick();
  assert.equal(w.document.getElementById('checkpoint-form').elements.goal.value, second.id);
});
