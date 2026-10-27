(() => {
  const results = { not_run: '미실행', partial: '일부 확인', failed: '문제 있음', blocked: '차단', passed: '통과', not_applicable: '해당 없음' };
  const statuses = { todo: '할 일', in_progress: '진행 중', needs_recheck: '재확인 필요', blocked: '차단', waiting_api: 'API 대기', waiting_decision: '기획 결정 대기', done: '완료', archived: '삭제됨' };
  const resultStatuses = { not_run: 'todo', partial: 'in_progress', failed: 'needs_recheck', blocked: 'blocked', passed: 'done', not_applicable: 'done' };
  const state = { tasks: [], selected: null, history: [], editing: null, busy: false, sequence: 0, initial: '', editorInitial: '' };
  const $ = (id) => document.getElementById(id);
  const escape = (value) => `${value ?? ''}`.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const signature = (id) => $(id) ? JSON.stringify([...new FormData($(id)).entries()]) : '';
  const resultDirty = () => Boolean($('result-form') && signature('result-form') !== state.initial);
  const today = () => {
    const now = new Date();
    return [now.getFullYear(), now.getMonth() + 1, now.getDate()].map((value) => `${value}`.padStart(2, '0')).join('-');
  };
  let toastTimer;
  function toast(message) {
    $('toast').textContent = message;
    $('toast').hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4000);
  }
  async function request(path, options = {}) {
    const response = await fetch(window.workUrl ? window.workUrl(path) : path, { ...options, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `요청에 실패했습니다. (${response.status})`);
    return data;
  }
  function canLeave() {
    return !state.busy && (!resultDirty() || window.confirm('저장하지 않은 QA 결과가 있습니다. 이동할까요?'));
  }
  function setBusy(value) {
    state.busy = value;
    if (value) state.sequence++;
    if ($('result-fields')) $('result-fields').disabled = value || state.selected?.status === 'archived';
    for (const element of $('scenario-form').elements) element.disabled = value;
    for (const id of ['add-scenario', 'refresh', 'edit-scenario', 'delete-scenario', 'restore-scenario']) {
      if ($(id)) $(id).disabled = value;
    }
    updateNavigation();
  }
  function visibleScenarios() {
    const filter = $('result-filter').value;
    const search = $('search').value.trim().toLocaleLowerCase();
    return state.tasks.filter((task) => {
      if (task.category !== 'qa') return false;
      if (filter === 'archived' ? task.status !== 'archived' : task.status === 'archived') return false;
      if (filter === 'remaining' && task.status === 'done') return false;
      if (filter === 'done' && task.status !== 'done') return false;
      if (filter === 'failed' && task.qa_result !== 'failed') return false;
      if ($('area-filter').value && !task.areas.includes($('area-filter').value)) return false;
      return !search || [task.id, task.title, task.summary, ...task.areas, task.qa.steps, task.qa.expected, task.qa.actual].join(' ').toLocaleLowerCase().includes(search);
    }).sort((a, b) => a.priority.localeCompare(b.priority) || a.id.localeCompare(b.id));
  }
  function renderCatalog() {
    const scenarios = state.tasks.filter((task) => task.category === 'qa' && task.status !== 'archived');
    const completed = scenarios.filter((task) => task.status === 'done').length;
    $('total-count').textContent = `${scenarios.length}`;
    $('remaining-count').textContent = `${scenarios.length - completed}`;
    $('completed-count').textContent = `${completed}`;
    $('failed-count').textContent = `${scenarios.filter((task) => task.qa_result === 'failed').length}`;
    $('qa-progress').max = Math.max(scenarios.length, 1);
    $('qa-progress').value = completed;
    $('progress-text').textContent = `${completed} / ${scenarios.length} 완료`;
    const area = $('area-filter').value;
    const areas = [...new Set(state.tasks.filter((task) => task.category === 'qa').flatMap((task) => task.areas))].sort((a, b) => a.localeCompare(b, 'ko'));
    $('area-filter').innerHTML = '<option value="">모든 영역</option>' + areas.map((name) => `<option value="${escape(name)}">${escape(name)}</option>`).join('');
    if (areas.includes(area)) $('area-filter').value = area;
    const visible = visibleScenarios();
    $('result-count').textContent = `${visible.length}건`;
    $('scenario-list').innerHTML = visible.length ? visible.map((task) => `<button class="scenario-item ${state.selected?.id === task.id ? 'active' : ''}" data-scenario="${escape(task.id)}" ${state.selected?.id === task.id ? 'aria-current="true"' : ''}><span class="scenario-item-meta"><span class="monospace">${escape(task.id)}</span><span class="badge ${escape(task.status)}">${escape(statuses[task.status])}</span></span><strong>${escape(task.title)}</strong><span class="scenario-item-meta">${escape(task.areas.join(' · ') || '공통')} · ${escape(results[task.qa_result])}</span></button>`).join('') : '<p class="qa-empty">조건에 맞는 시나리오가 없습니다.</p>';
    updateNavigation();
  }
  function navigationIds() {
    const ids = visibleScenarios().map((task) => task.id);
    const index = ids.indexOf(state.selected?.id);
    return { previous: index > 0 ? ids[index - 1] : null, next: index >= 0 ? ids[index + 1] : ids[0] };
  }
  function updateNavigation() {
    const { previous, next } = navigationIds();
    if ($('previous-scenario')) $('previous-scenario').disabled = !previous || state.busy;
    if ($('next-scenario')) $('next-scenario').disabled = !next || state.busy;
    if ($('save-next')) $('save-next').disabled = !next || state.busy || state.selected?.status === 'archived';
  }
  function prose(title, content, className = '') {
    return content?.trim() ? `<section class="${className}"><h3>${escape(title)}</h3><p class="prose">${escape(content)}</p></section>` : '';
  }
  function renderScenario() {
    const task = state.selected;
    if (!task) {
      $('scenario-content').innerHTML = '<div class="qa-empty"><h2>시나리오를 선택하세요</h2><p>왼쪽에서 테스트할 시나리오를 선택하거나 새로 추가하세요.</p></div>';
      state.initial = '';
      return;
    }
    const archived = task.status === 'archived';
    const steps = task.qa.steps.split(/\r?\n/).map((line) => line.replace(/^\s*(?:\d+[.)]|[-*])\s*/, '').trim()).filter(Boolean);
    const dependencies = task.blocked_by.map((id) => {
      const linked = state.tasks.find((item) => item.id === id);
      return { id, linked };
    });
    const missing = dependencies.filter(({ linked }) => linked?.status !== 'done');
    const dependencyNotice = dependencies.length ? `<div class="qa-blocker"><p>${missing.length ? '선행 확인이 남아 있습니다. 준비 조건을 확인한 뒤 실행하세요.' : '선행 항목 확인 완료'}</p><ul>${dependencies.map(({ id, linked }) => `<li>${escape(id)} · ${escape(linked?.title || '기록 확인 필요')} · ${escape(linked ? statuses[linked.status] : '미확인')}</li>`).join('')}</ul></div>` : '';
    const sources = task.sources.length ? `<section><h3>근거 문서·코드·티켓</h3><div class="qa-source-list">${task.sources.map((source) => source.url ? `<a href="${escape(source.url)}" target="_blank" rel="noopener noreferrer">${escape(source.label)} ↗</a>` : `<div><span class="muted">${escape(source.label)}</span><br><code>${escape(source.path)}</code></div>`).join('')}</div></section>` : '';
    $('scenario-content').innerHTML = `<header class="scenario-header"><div class="scenario-heading"><div><span class="monospace">${escape(task.id)}</span> <span class="badge ${escape(task.status)}">${escape(statuses[task.status])}</span><h2>${escape(task.title)}</h2></div><div class="scenario-actions">${archived ? '<button class="button secondary" id="restore-scenario">시나리오 복원</button>' : '<button class="button secondary" id="edit-scenario">시나리오 수정</button><button class="button quiet" id="delete-scenario">삭제</button>'}</div></div>${task.summary ? `<p class="prose muted">${escape(task.summary)}</p>` : ''}<div class="scenario-navigation"><button class="text-button" id="previous-scenario">← 이전 시나리오</button><span class="muted">${escape(task.areas.join(' · ') || '공통')}</span><button class="text-button" id="next-scenario">다음 시나리오 →</button></div></header><div class="qa-execution"><div class="qa-instructions">${archived ? '<div class="qa-blocker"><p>삭제한 시나리오입니다. 복원하면 다시 실행할 수 있습니다.</p></div>' : ''}${dependencyNotice}${prose('1. 준비 조건 / 진입 방법', task.qa.preconditions || '준비 조건이 없습니다. 시나리오 수정에서 실행 환경과 진입 방법을 추가하세요.')}<section><h3>2. 테스트 절차</h3>${steps.length ? `<ol class="qa-step-list">${steps.map((step) => `<li><span>${escape(step)}</span></li>`).join('')}</ol>` : '<p class="prose">등록된 테스트 절차가 없습니다. 시나리오를 수정하세요.</p>'}</section>${prose('기대 결과', task.qa.expected || '기대 결과를 등록하세요.', 'qa-expected')}${task.acceptance.trim() !== task.qa.expected.trim() ? prose('추가 완료 기준', task.acceptance) : ''}${prose('주의할 점 / 추가 안내', task.detail)}${prose('기존 확인 근거 / 검증 범위', task.verification)}${sources}</div><aside class="qa-result"><h3>3. 결과 / 문제 기록</h3><form id="result-form"><p id="result-error" class="banner error" role="alert" hidden></p><fieldset id="result-fields" ${archived ? 'disabled' : ''}><label>판정<select name="result" id="qa-result">${Object.entries(results).map(([value, label]) => `<option value="${value}" ${task.qa_result === value ? 'selected' : ''}>${label}</option>`).join('')}</select></label><label>확인일<input name="checked_at" type="date" required value="${escape(task.qa_result === 'not_run' ? today() : task.last_checked || today())}"></label><label>실제 결과 / 발견한 문제<textarea name="actual" rows="5" maxlength="30000" placeholder="예: 3단계에서 저장 후 새로고침하니 입력값이 사라짐">${escape(task.qa.actual)}</textarea></label><label>증거 · 선택<textarea name="evidence" rows="2" maxlength="30000" placeholder="스크린샷 경로, 확인 링크">${escape(task.qa.evidence)}</textarea></label><p class="muted">판정을 바꿔도 저장하기 전에는 반영되지 않습니다. 통과·해당 없음으로 저장하면 완료 처리됩니다. 미실행 외에는 실제 결과나 문제를 적어주세요.</p><div class="qa-result-buttons"><button type="submit" class="button primary" id="save-result" value="stay">결과 저장</button><button type="submit" class="button secondary" id="save-next" value="next">저장하고 다음 →</button></div></fieldset></form></aside></div><details class="qa-history"><summary>변경 및 실행 기록 · ${state.history.length}건</summary><ol>${state.history.map((item) => `<li>${escape(item.created_at)}<p>${escape(item.note || '기록 업데이트')}</p></li>`).join('')}</ol></details>`;
    $('result-form').addEventListener('submit', saveResult);
    state.initial = signature('result-form');
    updateNavigation();
  }
  function applySaved(data) {
    const index = state.tasks.findIndex((task) => task.id === data.task.id);
    if (index < 0) state.tasks.push(data.task);
    else state.tasks[index] = data.task;
    state.selected = data.task;
    state.history = data.history;
    $('saved-at').textContent = '저장 완료';
    renderCatalog();
    renderScenario();
    window.history.replaceState(null, '', `#${encodeURIComponent(data.task.id)}`);
  }
  async function selectScenario(id, force = false) {
    if (state.busy || (!force && !canLeave())) return;
    const sequence = ++state.sequence;
    $('scenario-content').innerHTML = '<p class="qa-empty">시나리오를 불러오는 중입니다.</p>';
    try {
      const data = await request(`/api/tasks/${encodeURIComponent(id)}`);
      if (sequence !== state.sequence) return;
      if (data.task.category !== 'qa') throw new Error('QA 시나리오가 아닙니다.');
      applySaved(data);
      $('saved-at').textContent = '최신 기록 불러옴';
    } catch (error) {
      if (sequence === state.sequence) {
        state.selected = null;
        $('scenario-content').innerHTML = `<div class="qa-empty"><p role="alert">${escape(error.message)}</p><button class="button secondary" data-scenario="${escape(id)}">다시 불러오기</button></div>`;
      }
    }
  }
  async function refresh() {
    if (!canLeave()) return;
    const sequence = ++state.sequence;
    $('refresh').disabled = true;
    try {
      const data = await request('/api/tasks');
      if (sequence !== state.sequence) return;
      state.tasks = data.tasks;
      $('connection-error').hidden = true;
      renderCatalog();
      const hash = decodeURIComponent(window.location.hash.slice(1));
      const selected = state.tasks.find((task) => task.category === 'qa' && task.id === (state.selected?.id || hash));
      const next = selected?.id || visibleScenarios()[0]?.id;
      if (next) await selectScenario(next, true);
      else { state.selected = null; renderScenario(); }
      $('saved-at').textContent = '최신 기록 불러옴';
    } catch (error) {
      $('connection-error').textContent = `기록을 불러오지 못했습니다. 다시 시도해주세요.\n${error.message}`;
      $('connection-error').hidden = false;
    } finally { $('refresh').disabled = state.busy; }
  }
  async function saveResult(event) {
    event.preventDefault();
    if (state.busy || !state.selected || state.selected.status === 'archived') return;
    const task = state.selected;
    const form = new FormData($('result-form'));
    const result = form.get('result');
    const actual = form.get('actual').trim();
    if (result !== 'not_run' && !actual) {
      $('result-error').textContent = '실제 결과나 발견한 문제를 짧게 기록해주세요.';
      $('result-error').hidden = false;
      return;
    }
    const next = event.submitter?.value === 'next' ? navigationIds().next : null;
    const done = resultStatuses[result] === 'done';
    setBusy(true);
    $('result-error').hidden = true;
    try {
      const data = await request(`/api/tasks/${task.id}`, { method: 'PATCH', body: JSON.stringify({
        version: task.version,
        status: result === 'not_run' ? (task.status === 'done' ? 'needs_recheck' : task.status) : resultStatuses[result],
        qa_result: result,
        qa: { ...task.qa, actual, evidence: form.get('evidence').trim() },
        last_checked: result === 'not_run' ? task.last_checked : form.get('checked_at'),
        resolution: done ? [task.resolution, `수동 QA (${results[result]})\n${actual}`].filter(Boolean).join('\n\n') : task.resolution,
        note: `수동 QA · ${results[result]}${actual ? `\n${actual.slice(0, 9000)}` : ''}`,
      }) });
      setBusy(false);
      applySaved(data);
      toast('QA 결과를 저장했습니다.');
      if (next) await selectScenario(next, true);
    } catch (error) {
      $('result-error').textContent = error.message;
      $('result-error').hidden = false;
    } finally {
      setBusy(false);
    }
  }
  function openEditor(edit) {
    if (!canLeave()) return;
    state.editing = edit ? state.selected : null;
    if (edit && (!state.editing || state.editing.status === 'archived')) return;
    const task = state.editing;
    $('scenario-form').reset();
    $('editor-title').textContent = task ? '시나리오 수정' : '시나리오 추가';
    $('editor-error').hidden = true;
    if (task) {
      const fields = $('scenario-form').elements;
      for (const key of ['title', 'summary', 'detail']) fields.namedItem(key).value = task[key];
      fields.namedItem('areas').value = task.areas.join(', ');
      for (const key of ['preconditions', 'steps', 'expected']) fields.namedItem(key).value = task.qa[key];
    }
    state.editorInitial = signature('scenario-form');
    $('scenario-editor').showModal();
    $('scenario-form').elements.namedItem('title').focus();
  }
  function closeEditor() {
    if (state.busy) return;
    if (signature('scenario-form') !== state.editorInitial && !window.confirm('저장하지 않은 시나리오를 닫을까요?')) return;
    $('scenario-editor').close();
  }
  async function saveScenario(event) {
    event.preventDefault();
    if (state.busy) return;
    const form = new FormData($('scenario-form'));
    const task = state.editing;
    const payload = {};
    for (const key of ['title', 'summary', 'detail']) payload[key] = form.get(key).trim();
    const qa = Object.fromEntries(['preconditions', 'steps', 'expected'].map((key) => [key, form.get(key).trim()]));
    if (!payload.title || Object.values(qa).some((value) => !value)) {
      $('editor-error').textContent = '이름·준비 조건·테스트 절차·기대 결과를 입력해주세요.';
      $('editor-error').hidden = false;
      return;
    }
    payload.areas = [...new Set(form.get('areas').split(',').map((area) => area.trim()).filter(Boolean))];
    payload.qa = { ...(task?.qa || { actual: '', evidence: '' }), ...qa };
    payload.note = task ? 'QA 시나리오 수정' : 'QA 시나리오 추가';
    if (task) payload.version = task.version;
    else Object.assign(payload, { category: 'qa', status: 'todo', qa_result: 'not_run', owner: 'QA', acceptance: qa.expected });
    setBusy(true);
    $('editor-error').hidden = true;
    try {
      const data = await request(task ? `/api/tasks/${task.id}` : '/api/tasks', { method: task ? 'PATCH' : 'POST', body: JSON.stringify(payload) });
      setBusy(false);
      $('scenario-editor').close();
      applySaved(data);
      toast('시나리오를 저장했습니다.');
    } catch (error) {
      $('editor-error').textContent = error.message;
      $('editor-error').hidden = false;
    } finally { setBusy(false); }
  }
  async function archiveScenario(restore) {
    if (!state.selected || !canLeave()) return;
    if (!restore && !window.confirm('시나리오를 삭제할까요? 실행 기록은 보관되며 삭제한 시나리오에서 복원할 수 있습니다.')) return;
    const task = state.selected;
    setBusy(true);
    try {
      const data = await request(`/api/tasks/${task.id}`, { method: 'PATCH', body: JSON.stringify({ version: task.version, status: restore ? 'needs_recheck' : 'archived', note: restore ? '삭제한 QA 시나리오 복원' : 'QA 시나리오 삭제 · 기록 보관' }) });
      setBusy(false);
      applySaved(data);
      toast(restore ? '시나리오를 복원했습니다.' : '시나리오를 삭제했습니다. 실행 기록은 보관됩니다.');
    } catch (error) { toast(error.message); }
    finally { setBusy(false); }
  }
  document.addEventListener('click', (event) => {
    const scenario = event.target.closest('[data-scenario]');
    if (scenario) selectScenario(scenario.dataset.scenario);
    else if (event.target.closest('#edit-scenario')) openEditor(true);
    else if (event.target.closest('#delete-scenario')) archiveScenario(false);
    else if (event.target.closest('#restore-scenario')) archiveScenario(true);
    else if (event.target.closest('#previous-scenario')) { const id = navigationIds().previous; if (id) selectScenario(id); }
    else if (event.target.closest('#next-scenario')) { const id = navigationIds().next; if (id) selectScenario(id); }
  });
  for (const id of ['search', 'result-filter', 'area-filter']) $(id).addEventListener(id === 'search' ? 'input' : 'change', renderCatalog);
  $('add-scenario').addEventListener('click', () => openEditor(false));
  $('refresh').addEventListener('click', refresh);
  $('close-editor').addEventListener('click', closeEditor);
  $('cancel-editor').addEventListener('click', closeEditor);
  $('scenario-editor').addEventListener('cancel', (event) => { event.preventDefault(); closeEditor(); });
  $('scenario-form').addEventListener('submit', saveScenario);
  window.addEventListener('beforeunload', (event) => {
    if (state.busy || resultDirty() || ($('scenario-editor').open && signature('scenario-form') !== state.editorInitial)) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
  refresh();
})();
