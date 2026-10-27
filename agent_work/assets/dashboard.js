(() => {
  const categories = { task: '작업', issue: '문제', decision: '기획 결정', api: 'API 확인·요청', qa: 'QA' };
  const statuses = {
    todo: '할 일',
    in_progress: '진행 중',
    waiting_decision: '기획 결정 대기',
    waiting_api: 'API 대기',
    needs_recheck: '재확인 필요',
    blocked: '차단',
    done: '완료',
    archived: '보관',
  };
  const qaResults = {
    not_run: '미실행',
    passed: '통과',
    failed: '실패',
    blocked: '차단',
    partial: '일부 확인',
    not_applicable: '해당 없음',
  };
  const state = {
    tasks: [],
    mode: 'all',
    actionable: false,
    selectedId: null,
    detailSequence: 0,
    editing: null,
    completing: null,
    completionInitial: '',
    saving: false,
    formInitial: '',
    loaded: false,
  };
  const $ = (id) => document.getElementById(id);
  const escape = (value) =>
    `${value ?? ''}`.replace(
      /[&<>"']/g,
      (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]
    );
  const closed = (task) => task.status === 'done' || task.status === 'archived';
  const missingDependencies = (task) =>
    task.blocked_by.filter((id) => state.tasks.find((item) => item.id === id)?.status !== 'done');
  const actionable = (task) =>
    ['todo', 'in_progress', 'needs_recheck'].includes(task.status) && !missingDependencies(task).length;
  const split = (value) => [
    ...new Set(
      value
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean)
    ),
  ];
  const badge = (value, labels) => `<span class="badge ${escape(value)}">${escape(labels[value] ?? value)}</span>`;
  const date = (value) =>
    value
      ? new Intl.DateTimeFormat('ko-KR', {
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
        }).format(new Date(value))
      : '—';
  let toastTimer;

  function toast(message) {
    $('toast').textContent = message;
    $('toast').hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      $('toast').hidden = true;
    }, 3500);
  }

  async function request(url, options = {}) {
    const response = await fetch(window.workUrl ? window.workUrl(url) : url, {
      ...options,
      headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `처리에 실패했습니다. (${response.status})`);
    return data;
  }

  function setOptions(target, options, first) {
    const current = target.value;
    target.innerHTML =
      (first ? `<option value="">${escape(first)}</option>` : '') +
      Object.entries(options)
        .map(([value, label]) => `<option value="${escape(value)}">${escape(label)}</option>`)
        .join('');
    if ([...target.options].some((option) => option.value === current)) target.value = current;
  }

  function render() {
    const open = state.tasks.filter((task) => !closed(task));
    const countCategory = (category) => open.filter((task) => task.category === category).length;
    const done = state.tasks.filter((task) => task.status === 'done').length;
    const items = [
      ['all', '전체 작업', '▦', open.length],
      ['issue', '문제', '◈', countCategory('issue')],
      ['decision', '기획 결정', '◇', countCategory('decision')],
      ['api', 'API 확인·요청', '⇄', countCategory('api')],
      ['qa', 'QA 체크리스트', '☑', countCategory('qa')],
      ['done', '완료 기록', '✓', done],
      ['archived', '보관함', '▤', state.tasks.filter((task) => task.status === 'archived').length],
    ];
    $('navigation').innerHTML = items
      .map(
        ([mode, label, icon, count], index) =>
          `${index === 5 ? '<div class="nav-divider"></div>' : ''}<button class="nav-item ${state.mode === mode ? 'active' : ''}" data-mode="${mode}" ${state.mode === mode ? 'aria-current="page"' : ''}><span class="nav-icon" aria-hidden="true">${icon}</span><span>${label}</span><span class="nav-count">${count}</span></button>`
      )
      .join('');
    const metrics = [
      ['issue', '확인할 문제', '◈', countCategory('issue'), '현재 문제와 재현 확인'],
      ['decision', '기획 결정', '◇', countCategory('decision'), '범위·동작·권한 합의'],
      ['api', 'API 확인·요청', '⇄', countCategory('api'), '서버 계약·응답 확인'],
      ['qa', '남은 QA', '☑', countCategory('qa'), '절차와 기대 결과 확인'],
      ['done', '완료 기록', '✓', done, '처리 근거와 결정 보관'],
    ];
    $('metrics').innerHTML = metrics
      .map(
        ([mode, label, icon, count, hint]) =>
          `<button class="metric ${mode}" data-mode="${mode}"><span class="metric-top">${label}<span class="metric-icon" aria-hidden="true">${icon}</span></span><strong class="metric-number">${count}</strong><span class="metric-note">${hint}</span></button>`
      )
      .join('');
    const ready = open.filter(actionable).length;
    $('actionable-count').textContent = `${ready}건`;
    $('work-summary-text').textContent =
      `기획·API 대기는 별도로 관리 · 우선순위 P1 ${open.filter((task) => task.priority === 'P1').length}건`;
    $('actionable-toggle').setAttribute('aria-pressed', `${state.actionable}`);
    $('actionable-toggle').textContent = state.actionable ? '실행 가능 필터 해제 ×' : '실행 가능한 항목만 보기 →';
    const areas = [...new Set(state.tasks.flatMap((task) => task.areas))].sort((a, b) => a.localeCompare(b, 'ko'));
    setOptions($('area-filter'), Object.fromEntries(areas.map((area) => [area, area])), '모든 영역');
    const search = $('search').value.trim().toLocaleLowerCase();
    const visible = state.tasks
      .filter((task) => {
        if (state.mode === 'done' || state.mode === 'archived') {
          if (task.status !== state.mode) return false;
        } else {
          if (!$('include-closed').checked && closed(task)) return false;
          if (state.mode !== 'all' && task.category !== state.mode) return false;
        }
        if (state.actionable && !actionable(task)) return false;
        if ($('status-filter').value && task.status !== $('status-filter').value) return false;
        if ($('area-filter').value && !task.areas.includes($('area-filter').value)) return false;
        if ($('priority-filter').value && task.priority !== $('priority-filter').value) return false;
        return (
          !search ||
          [
            task.id,
            task.title,
            task.summary,
            task.detail,
            task.next_action,
            task.owner,
            ...task.areas,
            task.qa.steps,
            task.qa.expected,
          ]
            .join(' ')
            .toLocaleLowerCase()
            .includes(search)
        );
      })
      .sort(
        (a, b) =>
          a.priority.localeCompare(b.priority) ||
          Number(actionable(b)) - Number(actionable(a)) ||
          a.id.localeCompare(b.id)
      );
    $('list-title').textContent = items.find(([mode]) => mode === state.mode)?.[1] ?? '전체 작업';
    $('result-count').textContent = `${visible.length}건`;
    $('task-rows').innerHTML = visible.length
      ? visible
          .map(
            (task) =>
              `<tr data-task-id="${escape(task.id)}"><td><span class="priority ${escape(task.priority)}">${escape(task.priority)}</span></td><td><button class="task-title" data-open="${escape(task.id)}">${escape(task.title)}</button><div class="task-meta"><span class="monospace">${escape(task.id)}</span><span>·</span><span>${escape(task.areas.join(' · ') || '공통')}</span>${missingDependencies(task).length ? `<span class="dependency-tag">· 선행 ${missingDependencies(task).length}건</span>` : ''}${task.category === 'qa' ? `<span>· ${escape(qaResults[task.qa_result])}</span>` : ''}</div></td><td>${badge(task.category, categories)}</td><td>${badge(task.status, statuses)}</td><td>${escape(task.owner || '미지정')}</td><td class="date">${escape(task.last_checked || '미확인')}</td><td>${task.category === 'qa' && !closed(task) ? `<button class="button secondary" data-complete-qa="${escape(task.id)}" aria-label="${escape(task.id)} QA 완료 처리">QA 완료 처리</button>` : '<span class="arrow" aria-hidden="true">›</span>'}</td></tr>`
          )
          .join('')
      : `<tr><td colspan="7" class="empty">${state.loaded ? '조건에 맞는 항목이 없습니다.' : '기록을 불러오는 중입니다.'}</td></tr>`;
  }

  async function refresh(notify = true) {
    $('refresh').disabled = true;
    try {
      const data = await request('/api/tasks');
      state.tasks = data.tasks;
      state.loaded = true;
      $('connection-error').hidden = true;
      $('saved-at').textContent = `SQLite · ${date(data.saved_at)} 불러옴`;
      render();
      if (notify) toast('최신 기록을 불러왔습니다.');
    } catch (error) {
      $('connection-error').textContent =
        `대시보드 서버에 연결하지 못했습니다. Python 서버 실행 상태를 확인해주세요.\n${error.message}`;
      $('connection-error').hidden = false;
      $('saved-at').textContent = '연결 실패 · 저장 상태 확인 필요';
    } finally {
      $('refresh').disabled = false;
    }
  }

  const section = (title, content) =>
    content
      ? `<section class="detail-section"><h3>${escape(title)}</h3><p class="prose">${escape(content)}</p></section>`
      : '';
  async function openDetail(id) {
    const sequence = ++state.detailSequence;
    state.selectedId = id;
    $('detail-id').textContent = id;
    $('detail-content').innerHTML = '<h2 id="detail-title">불러오는 중…</h2>';
    if (!$('detail-panel').open) $('detail-panel').showModal();
    try {
      const { task, history } = await request(`/api/tasks/${encodeURIComponent(id)}`);
      if (sequence !== state.detailSequence) return;
      const sources = task.sources
        .map((source, index) =>
          source.url
            ? `<a class="source-link" href="${escape(source.url)}" target="_blank" rel="noopener noreferrer">${escape(source.label)} ↗</a>`
            : `<button class="source-link" data-source-index="${index}">${escape(source.label)} ↗</button>`
        )
        .join('');
      const dependencies = task.blocked_by
        .map((dependency) => {
          const linked = state.tasks.find((item) => item.id === dependency);
          return `<button class="source-link" data-open="${escape(dependency)}">${escape(dependency)} · ${escape(linked ? statuses[linked.status] : '기록 확인 필요')}</button>`;
        })
        .join('');
      $('detail-content').innerHTML =
        `<div class="detail-badges"><span class="priority ${escape(task.priority)}">${escape(task.priority)}</span>${badge(task.category, categories)}${badge(task.status, statuses)}</div><h2 id="detail-title">${escape(task.title)}</h2><p class="detail-summary">${escape(task.summary)}</p><div class="detail-actions">${task.category === 'qa' && !closed(task) ? `<button class="button primary" data-complete-qa="${escape(id)}">QA 완료 처리</button>` : ''}<button class="button ${task.category === 'qa' && !closed(task) ? 'secondary' : 'primary'}" data-edit="${escape(id)}">내용·상태 수정</button>${task.category === 'issue' ? '<button class="button secondary" data-copy-task>에이전트용 복사</button>' : ''}<button class="button secondary" data-archive="${escape(id)}">${task.status === 'archived' ? '보관 해제' : '보관'}</button></div><div class="detail-facts"><div><span>영역</span><strong>${escape(task.areas.join(' · ') || '공통')}</strong></div><div><span>담당 구분</span><strong>${escape(task.owner || '미지정')}</strong></div><div><span>최근 확인</span><strong>${escape(task.last_checked || '미확인')}</strong></div><div><span>확인 근거</span><strong>${escape(task.verification || '미기록')}</strong></div></div>${section('상세 / 현재 제약', task.detail)}${section('다음 할 일', task.next_action)}${section('완료·확인 기준', task.acceptance)}${dependencies ? `<section class="detail-section"><h3>선행 항목</h3><div class="source-list">${dependencies}</div></section>` : ''}${task.category === 'qa' ? section(`QA 판정 · ${qaResults[task.qa_result]}`, '준비 조건\n' + (task.qa.preconditions || '미기록')) + section('테스트 절차', task.qa.steps) + section('기대 결과', task.qa.expected) + section('실제 결과', task.qa.actual || '아직 실행 결과가 기록되지 않았습니다.') + section('증거', task.qa.evidence) : ''}${section('처리·결정 내용', task.resolution)}${sources ? `<section class="detail-section"><h3>근거 문서·티켓</h3><div class="source-list">${sources}</div></section>` : ''}<section class="detail-section"><h3>변경 이력 <span class="muted">버전 ${task.version}</span></h3><ol class="history">${history.map((item) => `<li>${escape(date(item.created_at))} · ${item.action === 'created' ? '등록' : '수정'}<p>${escape(item.note || '기록 업데이트')}</p></li>`).join('')}</ol></section>`;
      $('detail-content')
        .querySelectorAll('[data-source-index]')
        .forEach((button) =>
          button.addEventListener('click', () => openSource(task.sources[Number(button.dataset.sourceIndex)]))
        );
      const copyButton = $('detail-content').querySelector('[data-copy-task]');
      if (copyButton) copyButton.addEventListener('click', () => copyTask(task, copyButton));
      const index = state.tasks.findIndex((item) => item.id === id);
      if (index >= 0) state.tasks[index] = task;
    } catch (error) {
      if (sequence === state.detailSequence)
        $('detail-content').innerHTML =
          `<h2 id="detail-title">불러오기 실패</h2><p class="banner error">${escape(error.message)}</p>`;
    }
  }

  async function copyTask(task, button) {
    const dependencies = task.blocked_by
      .map((id) => {
        const linked = state.tasks.find((item) => item.id === id);
        return `- ${id}${linked ? ` · ${linked.title} (${statuses[linked.status]})` : ' · 기록 확인 필요'}`;
      })
      .join('\n');
    const sources = task.sources
      .map((source) => `- ${source.label}: ${source.url || source.path || ''}`)
      .join('\n');
    const sections = [
      ['문제 요약', task.summary],
      ['상세 / 현재 제약', task.detail],
      ['다음 할 일', task.next_action],
      ['완료 기준', task.acceptance],
      ['확인 근거 / 검증 범위', task.verification],
      ['선행 항목', dependencies],
      ['처리·결정 내용', task.resolution],
      ['근거 문서·코드·티켓', sources],
    ]
      .filter(([, content]) => content.trim())
      .map(([title, content]) => `## ${title}\n${content.trim()}`);
    const context = [
      `상태: ${statuses[task.status]}`,
      task.areas.length ? `영역: ${task.areas.join(', ')}` : '',
      task.last_checked ? `최근 확인일: ${task.last_checked}` : '',
    ].filter(Boolean).join('\n');
    const text = [`# ${task.id} · ${task.title}`, context, ...sections].join('\n\n');
    button.disabled = true;
    button.textContent = '복사 중…';
    try {
      if (!navigator.clipboard?.writeText) throw new Error('클립보드를 사용할 수 없습니다.');
      await navigator.clipboard.writeText(text);
      toast('에이전트에게 전달할 내용을 복사했습니다.');
    } catch {
      toast('복사하지 못했습니다. 브라우저의 클립보드 권한을 확인해주세요.');
    } finally {
      button.disabled = false;
      button.textContent = '에이전트용 복사';
    }
  }

  async function openSource(source) {
    $('source-title').textContent = source.path;
    $('source-text').textContent = '문서를 불러오는 중입니다.';
    $('source-dialog').showModal();
    try {
      $('source-text').textContent = (await request(`/api/source?path=${encodeURIComponent(source.path)}`)).text;
    } catch (error) {
      $('source-text').textContent = error.message;
    }
  }

  function formSignature(id = 'task-form') {
    return JSON.stringify([...new FormData($(id)).entries()]);
  }
  function openEditor(id = null) {
    const task = id ? state.tasks.find((item) => item.id === id) : null;
    if (id && !task) return toast('최신 목록을 불러온 뒤 다시 선택해주세요.');
    state.editing = task;
    $('task-form').reset();
    $('editor-title').textContent = task ? `${task.id} 수정` : '항목 추가';
    $('editor-error').hidden = true;
    const fields = $('task-form').elements;
    if (task) {
      for (const key of [
        'title',
        'summary',
        'detail',
        'next_action',
        'acceptance',
        'owner',
        'last_checked',
        'verification',
        'resolution',
        'category',
        'status',
        'priority',
        'qa_result',
      ])
        fields.namedItem(key).value = task[key];
      for (const key of ['areas', 'blocked_by']) fields.namedItem(key).value = task[key].join(', ');
      fields.namedItem('sources').value = task.sources
        .map((source) => `${source.label} | ${source.url || source.path || ''}`)
        .join('\n');
      for (const [key, value] of Object.entries(task.qa)) fields.namedItem(`qa_${key}`).value = value;
    } else {
      fields.namedItem('category').value = categories[state.mode] ? state.mode : 'issue';
      fields.namedItem('status').value =
        state.mode === 'decision' ? 'waiting_decision' : state.mode === 'api' ? 'waiting_api' : 'todo';
    }
    $('qa-fields').hidden = fields.namedItem('category').value !== 'qa';
    state.formInitial = formSignature();
    $('editor').showModal();
    fields.namedItem('title').focus();
  }

  function closeEditor() {
    if (state.saving) return;
    if (formSignature() !== state.formInitial && !window.confirm('저장하지 않은 변경을 닫을까요?')) return;
    $('editor').close();
  }

  function openQaCompletion(id) {
    const task = state.tasks.find((item) => item.id === id);
    if (!task || task.category !== 'qa' || closed(task)) return;
    state.completing = task;
    $('qa-completion-form').reset();
    $('qa-completion-task').textContent = `${task.id} · ${task.title}`;
    $('qa-completion-error').hidden = true;
    const fields = $('qa-completion-form').elements;
    fields.namedItem('result').value = task.qa_result === 'not_applicable' ? 'not_applicable' : 'passed';
    fields.namedItem('actual').value = task.qa.actual;
    fields.namedItem('evidence').value = task.qa.evidence;
    const today = new Date();
    fields.namedItem('checked_at').value = [today.getFullYear(), today.getMonth() + 1, today.getDate()]
      .map((part) => `${part}`.padStart(2, '0'))
      .join('-');
    state.completionInitial = formSignature('qa-completion-form');
    $('qa-completion').showModal();
    fields.namedItem('actual').focus();
  }

  function closeQaCompletion() {
    if (state.saving) return;
    if (
      formSignature('qa-completion-form') !== state.completionInitial &&
      !window.confirm('저장하지 않은 QA 결과를 닫을까요?')
    ) return;
    $('qa-completion').close();
    state.completing = null;
  }

  async function completeQa(event) {
    event.preventDefault();
    if (state.saving || !state.completing) return;
    const task = state.completing;
    const form = new FormData($('qa-completion-form'));
    const actual = form.get('actual').trim();
    if (!actual) {
      $('qa-completion-error').textContent = '직접 확인한 실제 결과를 입력해주세요.';
      $('qa-completion-error').hidden = false;
      $('qa-completion-form').elements.namedItem('actual').focus();
      return;
    }
    const result = form.get('result');
    const completion = `QA 완료 (${qaResults[result]})\n${actual}`;
    state.saving = true;
    $('save-qa-completion').disabled = true;
    $('save-qa-completion').textContent = '저장 중…';
    $('qa-completion-error').hidden = true;
    try {
      const { task: saved } = await request(`/api/tasks/${task.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          version: task.version,
          status: 'done',
          qa_result: result,
          qa: { ...task.qa, actual, evidence: form.get('evidence').trim() },
          last_checked: form.get('checked_at'),
          resolution: [task.resolution, completion].filter(Boolean).join('\n\n'),
          note: `사용자 QA 완료 처리 · ${qaResults[result]}`,
        }),
      });
      state.tasks = state.tasks.map((item) => (item.id === saved.id ? saved : item));
      $('qa-completion').close();
      state.completing = null;
      render();
      $('saved-at').textContent = `SQLite · ${date(saved.updated_at)} 저장`;
      toast('QA를 완료 처리했습니다. 완료 기록에서 확인할 수 있습니다.');
      await openDetail(saved.id);
    } catch (error) {
      $('qa-completion-error').textContent = error.message;
      $('qa-completion-error').hidden = false;
      $('qa-completion-error').scrollIntoView?.({ block: 'nearest' });
    } finally {
      state.saving = false;
      $('save-qa-completion').disabled = false;
      $('save-qa-completion').textContent = '완료 저장';
    }
  }

  async function save(event) {
    event.preventDefault();
    if (state.saving) return;
    const form = new FormData($('task-form'));
    const payload = {};
    for (const key of [
      'title',
      'summary',
      'detail',
      'next_action',
      'acceptance',
      'owner',
      'last_checked',
      'verification',
      'resolution',
      'category',
      'status',
      'priority',
      'qa_result',
      'note',
    ])
      payload[key] = form.get(key).trim();
    payload.areas = split(form.get('areas'));
    payload.blocked_by = split(form.get('blocked_by'));
    payload.sources = form
      .get('sources')
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const separator = line.indexOf('|');
        const label = separator < 0 ? line : line.slice(0, separator).trim();
        const target = separator < 0 ? line : line.slice(separator + 1).trim();
        return /^https?:\/\//i.test(target) ? { label, url: target } : { label, path: target };
      });
    payload.qa = Object.fromEntries(
      ['preconditions', 'steps', 'expected', 'actual', 'evidence'].map((key) => [key, form.get(`qa_${key}`).trim()])
    );
    if (state.editing) payload.version = state.editing.version;
    state.saving = true;
    $('save-task').disabled = true;
    $('save-task').textContent = '저장 중…';
    $('editor-error').hidden = true;
    try {
      const { task } = await request(state.editing ? `/api/tasks/${state.editing.id}` : '/api/tasks', {
        method: state.editing ? 'PATCH' : 'POST',
        body: JSON.stringify(payload),
      });
      const index = state.tasks.findIndex((item) => item.id === task.id);
      if (index >= 0) state.tasks[index] = task;
      else state.tasks.push(task);
      $('editor').close();
      render();
      toast('기록을 저장했습니다.');
      $('saved-at').textContent = `SQLite · ${date(task.updated_at)} 저장`;
      await openDetail(task.id);
    } catch (error) {
      $('editor-error').textContent = error.message;
      $('editor-error').hidden = false;
      $('editor-error').scrollIntoView?.({ block: 'nearest' });
    } finally {
      state.saving = false;
      $('save-task').disabled = false;
      $('save-task').textContent = '저장';
    }
  }

  async function archive(id) {
    const task = state.tasks.find((item) => item.id === id);
    if (
      !task ||
      !window.confirm(
        task.status === 'archived'
          ? '재확인 필요 상태로 보관을 해제할까요?'
          : '보관함으로 이동할까요? 기록과 이력은 유지됩니다.'
      )
    )
      return;
    try {
      const result = await request(`/api/tasks/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          version: task.version,
          status: task.status === 'archived' ? 'needs_recheck' : 'archived',
          note: task.status === 'archived' ? '보관 해제' : '보관함으로 이동',
        }),
      });
      state.tasks = state.tasks.map((item) => (item.id === id ? result.task : item));
      render();
      await openDetail(id);
      toast('상태를 저장했습니다.');
    } catch (error) {
      toast(error.message);
    }
  }

  setOptions($('status-filter'), statuses, '모든 상태');
  setOptions($('edit-category'), categories);
  setOptions($('edit-status'), statuses);
  setOptions($('edit-qa-result'), qaResults);
  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-mode], [data-open], [data-edit], [data-archive], [data-complete-qa]');
    if (!button) return;
    if (button.dataset.mode) {
      state.mode = button.dataset.mode;
      state.actionable = false;
      $('status-filter').value = '';
      render();
    } else if (button.dataset.open) openDetail(button.dataset.open);
    else if (button.dataset.completeQa) openQaCompletion(button.dataset.completeQa);
    else if (button.dataset.edit) openEditor(button.dataset.edit);
    else if (button.dataset.archive) archive(button.dataset.archive);
  });
  for (const id of ['search', 'status-filter', 'area-filter', 'priority-filter', 'include-closed'])
    $(id).addEventListener(id === 'search' ? 'input' : 'change', render);
  $('reset-filters').addEventListener('click', () => {
    for (const id of ['search', 'status-filter', 'area-filter', 'priority-filter']) $(id).value = '';
    $('include-closed').checked = false;
    state.actionable = false;
    render();
  });
  $('actionable-toggle').addEventListener('click', () => {
    state.mode = 'all';
    state.actionable = !state.actionable;
    render();
  });
  $('add-task').addEventListener('click', () => openEditor());
  $('refresh').addEventListener('click', () => refresh());
  $('close-detail').addEventListener('click', () => $('detail-panel').close());
  $('detail-panel').addEventListener('close', () => {
    state.detailSequence++;
    state.selectedId = null;
  });
  $('edit-category').addEventListener('change', () => {
    $('qa-fields').hidden = $('edit-category').value !== 'qa';
  });
  $('close-editor').addEventListener('click', closeEditor);
  $('cancel-edit').addEventListener('click', closeEditor);
  $('editor').addEventListener('cancel', (event) => {
    event.preventDefault();
    closeEditor();
  });
  $('close-source').addEventListener('click', () => $('source-dialog').close());
  $('task-form').addEventListener('submit', save);
  $('close-qa-completion').addEventListener('click', closeQaCompletion);
  $('cancel-qa-completion').addEventListener('click', closeQaCompletion);
  $('qa-completion').addEventListener('cancel', (event) => {
    event.preventDefault();
    closeQaCompletion();
  });
  $('qa-completion-form').addEventListener('submit', completeQa);
  document.addEventListener('keydown', (event) => {
    if (
      event.key === '/' &&
      !document.querySelector('dialog[open]') &&
      !event.target.closest('input,textarea,select')
    ) {
      event.preventDefault();
      $('search').focus();
    }
  });
  window.addEventListener('beforeunload', (event) => {
    if (
      state.saving ||
      ($('editor').open && formSignature() !== state.formInitial) ||
      ($('qa-completion').open && formSignature('qa-completion-form') !== state.completionInitial)
    ) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
  render();
  refresh(false);
})();
