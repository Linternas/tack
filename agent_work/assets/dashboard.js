(() => {
  const queues = {todo:'할 일',in_progress:'진행 중',issue:'문제',qa:'QA',waiting:'대기',done:'완료',archived:'보관'};
  const relationLabels = {parent:'상위 작업',child:'하위 작업',blocked_by:'선행 작업',blocks:'후속 작업',duplicate_of:'중복 원본',duplicated_by:'중복으로 연결된 작업',superseded_by:'통합·대체 대상',supersedes:'통합·대체한 작업',relates_to:'관련 작업'};
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
    queue: '',
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
  const closed = (task) => task.queue === 'done' || task.queue === 'archived';
  const missingDependencies = (task) =>
    task.blocked_by.filter((id) => state.tasks.find((item) => item.id === id)?.queue !== 'done');
  const actionable = (task) =>
    ['todo', 'in_progress'].includes(task.queue) && !missingDependencies(task).length;
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
    const apiOnly = $('api-only').checked;
    $('metrics').setAttribute('aria-label',apiOnly ? 'API 요청 업무 목록 요약' : '개발 현황 요약');
    const scoped = state.tasks.filter(task => !apiOnly || task.is_api_request);
    const open = scoped.filter((task) => !closed(task));
    const metrics = [
      ['todo','grid','착수하기로 정한 작업'],
      ['in_progress','qa','현재 수행 중인 작업'],
      ['issue','issue','조사·처리할 문제'],
      ['qa','qa','검증할 항목'],
      ['waiting','api','결정·외부 조건 대기'],
    ];
    $('metrics').innerHTML = metrics
      .map(([queue, icon, hint]) => {
        const count = open.filter((task) => task.queue === queue).length;
        const selected = state.mode === 'all' && state.queue === queue;
        return `<button class="metric ${queue}" data-queue="${queue}" aria-pressed="${selected}"><span class="metric-top">${queues[queue]}<span class="metric-icon" aria-hidden="true">${window.tackIcon?.(icon)||''}</span></span><strong class="metric-number">${count}</strong><span class="metric-note">${hint}</span></button>`;
      })
      .join('');
    const areas = [...new Set(state.tasks.flatMap((task) => task.areas))].sort((a, b) => a.localeCompare(b, 'ko'));
    setOptions($('area-filter'), Object.fromEntries(areas.map((area) => [area, area])), '모든 영역');
    const search = $('search').value.trim().toLocaleLowerCase();
    const idSearch = /^[a-z][a-z0-9]*-\d{4,}$/i.test(search) || state.tasks.some((task) => task.id.toLocaleLowerCase() === search);
    const visible = scoped
      .filter((task) => {
        if (state.mode === 'done' || state.mode === 'archived') {
          if (task.queue !== state.mode) return false;
        } else if (state.mode !== 'everything') {
          if (closed(task)) return false;
        }
        if (state.queue && task.queue !== state.queue) return false;
        if ($('area-filter').value && !task.areas.includes($('area-filter').value)) return false;
        if ($('priority-filter').value && task.priority !== $('priority-filter').value) return false;
        if (idSearch) return task.id.toLocaleLowerCase() === search;
        return (
          !search ||
          [
            task.id,
            task.title,
            task.page_path,
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
    $('list-title').textContent = state.mode === 'all' && state.queue
      ? queues[state.queue]
      : ({all: '미완료 목록', everything: '전체 목록', done: '완료 기록', archived: '보관함'})[state.mode];
    if (apiOnly) $('list-title').textContent = 'API 요청 · '+$('list-title').textContent;
    $('result-count').textContent = `${visible.length}건`;
    $('task-rows').innerHTML = visible.length
      ? visible
          .map(
            (task) =>
              `<tr data-task-id="${escape(task.id)}"><td><span class="priority ${escape(task.priority)}">${escape(task.priority)}</span></td><td><button class="task-title" data-open="${escape(task.id)}">${escape(task.title)}</button>${task.page_path ? `<code class="task-page-path" title="${escape(task.page_path)}">${escape(task.page_path)}</code>` : ''}<div class="task-meta"><span class="monospace">${escape(task.id)}</span><span>·</span><span>${escape(task.areas.join(' · ') || '공통')}</span>${task.is_api_request ? '<span class="badge api">API 요청</span>' : ''}${task.parent_id ? `<button class="source-link" data-open="${escape(task.parent_id)}">상위 ${escape(task.parent_id)}</button>` : ''}${(task.relations || []).filter(link => link.type !== 'relates_to').map(link => `<button class="source-link" data-open="${escape(link.task_id)}">${escape(relationLabels[link.type])} ${escape(link.task_id)}</button>`).join('')}${missingDependencies(task).length ? `<span class="dependency-tag">· 선행 ${missingDependencies(task).length}건</span>` : ''}</div></td><td>${badge(task.queue, queues)}</td><td>${escape(task.owner || '미지정')}</td><td class="date">${escape(task.last_checked || '미확인')}</td><td>${task.queue === 'qa' ? `<button class="button secondary" data-complete-qa="${escape(task.id)}" aria-label="${escape(task.id)} QA 완료 처리">QA 완료 처리</button>` : '<span class="arrow" aria-hidden="true">›</span>'}</td></tr>`
          )
          .join('')
      : `<tr><td colspan="6" class="empty">${state.loaded ? '조건에 맞는 항목이 없습니다.' : '기록을 불러오는 중입니다.'}</td></tr>`;
  }

  function searchChanged() {
    const query = $('search').value.trim().toLocaleLowerCase();
    const exact = state.tasks.find((task) => task.id.toLocaleLowerCase() === query);
    if (exact) {
      if (state.mode !== 'everything') state.mode = closed(exact) ? exact.queue : 'all';
      state.queue = '';
      if (!exact.is_api_request) $('api-only').checked = false;
      $('record-view').value = state.mode;
      for (const id of ['area-filter', 'priority-filter']) $(id).value = '';
    }
    render();
  }

  async function refresh(notify = true) {
    $('refresh').disabled = true;
    try {
      const data = await request('/api/tasks');
      state.tasks = data.tasks;
      state.loaded = true;
      $('connection-error').hidden = true;
      $('saved-at').textContent = `SQLite · ${date(data.saved_at)} 불러옴`;
      searchChanged();
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
  function relationshipSection(task, relationships) {
    const options = ['parent','blocked_by','duplicate_of','superseded_by','relates_to'];
    const items = relationships.items.map(item => `<li class="relation-row"><span>${escape(relationLabels[item.type])}</span><button class="source-link" data-open="${escape(item.id)}">${escape(item.id)} · ${escape(item.title)}</button>${badge(item.queue,queues)}${item.outgoing ? `<button class="icon-button" data-unlink="${escape(item.type)}" data-target="${escape(item.id)}" aria-label="${escape(item.id)} ${escape(relationLabels[item.type])} 관계 해제">×</button>` : ''}</li>`).join('');
    return `<section class="detail-section"><div class="detail-actions"><h3>작업 관계</h3><button class="button secondary" data-add-subtask ${task.queue === 'done' ? 'disabled title="상위 작업을 재개한 뒤 하위 작업을 추가하세요."' : ''}>하위 작업 만들기</button></div><ul class="relation-list">${items || '<li class="muted">연결된 작업이 없습니다.</li>'}</ul><div class="relation-form"><select id="relation-type" aria-label="작업 관계 유형">${options.map(type => `<option value="${type}">${relationLabels[type]}</option>`).join('')}</select><input id="relation-target" aria-label="관계 대상 작업 ID" placeholder="연결할 작업 ID"><button id="relation-add" class="button secondary">관계 연결</button></div><p id="relation-error" class="banner error" role="alert" hidden></p></section>`;
  }

  async function saveRelation(task, type, target, remove) {
    const panel = $('detail-content');
    const buttons = [...panel.querySelectorAll('#relation-add,[data-unlink]')];
    buttons.forEach(button => { button.disabled = true; });
    $('relation-error').hidden = true;
    try {
      const result = await request(`/api/tasks/${encodeURIComponent(task.id)}/relations`, {
        method:'POST', body:JSON.stringify({type,target,version:task.version,remove}),
      });
      const index = state.tasks.findIndex(item => item.id === task.id);
      if (index >= 0) state.tasks[index] = result.task;
      render();
      if (state.selectedId === task.id) await openDetail(task.id);
      toast(remove ? '관계를 해제했습니다.' : '작업을 연결했습니다.');
    } catch (error) {
      if (state.selectedId === task.id && $('relation-error')) {
        $('relation-error').textContent = error.message;
        $('relation-error').hidden = false;
      }
    } finally {
      buttons.forEach(button => { button.disabled = false; });
    }
  }

  async function openDetail(id) {
    const sequence = ++state.detailSequence;
    state.selectedId = id;
    $('detail-id').textContent = id;
    $('detail-content').innerHTML = '<h2 id="detail-title">불러오는 중…</h2>';
    if (!$('detail-panel').open) $('detail-panel').showModal();
    try {
      const { task, history, relationships = {items:[],counts:{}} } = await request(`/api/tasks/${encodeURIComponent(id)}`);
      if (sequence !== state.detailSequence) return;
      const sources = task.sources
        .map((source, index) =>
          source.url
            ? `<a class="source-link" href="${escape(source.url)}" target="_blank" rel="noopener noreferrer">${escape(source.label)} ↗</a>`
            : `<button class="source-link" data-source-index="${index}">${escape(source.label)} ↗</button>`
        )
        .join('');
      $('detail-content').innerHTML =
        `<div class="detail-badges"><span class="priority ${escape(task.priority)}">${escape(task.priority)}</span>${badge(task.queue, queues)}${task.is_api_request ? '<span class="badge api">API 요청</span>' : ''}</div><h2 id="detail-title">${escape(task.title)}</h2><p class="detail-summary">${escape(task.summary)}</p><section class="detail-page-path"><h3>페이지 경로</h3><code>${escape(task.page_path || '미기록')}</code></section><div class="detail-actions">${task.queue === 'qa' ? `<button class="button primary" data-complete-qa="${escape(id)}">QA 완료 처리</button>` : ''}<button class="button ${task.queue === 'qa' ? 'secondary' : 'primary'}" data-edit="${escape(id)}">내용·상태 수정</button>${task.queue === 'issue' || task.category === 'issue' ? '<button class="button secondary" data-copy-task>에이전트용 복사</button>' : ''}<button class="button secondary" data-archive="${escape(id)}">${task.queue === 'archived' ? '보관 해제' : '보관'}</button></div><div class="detail-facts"><div><span>영역</span><strong>${escape(task.areas.join(' · ') || '공통')}</strong></div><div><span>담당 구분</span><strong>${escape(task.owner || '미지정')}</strong></div><div><span>최근 확인</span><strong>${escape(task.last_checked || '미확인')}</strong></div><div><span>확인 근거</span><strong>${escape(task.verification || '미기록')}</strong></div></div>${relationshipSection(task, relationships)}${section('상세 / 현재 제약', task.detail)}${section('다음 할 일', task.next_action)}${section('완료·확인 기준', task.acceptance)}${task.category === 'qa' ? section(`QA 판정 · ${qaResults[task.qa_result]}`, '준비 조건\n' + (task.qa.preconditions || '미기록')) + section('테스트 절차', task.qa.steps) + section('기대 결과', task.qa.expected) + section('실제 결과', task.qa.actual || '아직 실행 결과가 기록되지 않았습니다.') + section('증거', task.qa.evidence) : ''}${section('처리·결정 내용', task.resolution)}${sources ? `<section class="detail-section"><h3>근거 문서·티켓</h3><div class="source-list">${sources}</div></section>` : ''}<section class="detail-section"><h3>변경 이력 <span class="muted">버전 ${task.version}</span></h3><ol class="history">${history.map((item) => `<li>${escape(date(item.created_at))} · ${item.action === 'created' ? '등록' : '수정'}<p>${escape(item.note || '기록 업데이트')}</p></li>`).join('')}</ol></section>`;
      $('detail-content')
        .querySelectorAll('[data-source-index]')
        .forEach((button) =>
          button.addEventListener('click', () => openSource(task.sources[Number(button.dataset.sourceIndex)]))
        );
      $('detail-content').querySelector('[data-add-subtask]').addEventListener('click', () => openEditor(null, task.id));
      $('detail-content').querySelector('#relation-add').addEventListener('click', () => {
        const type = $('relation-type').value;
        const target = $('relation-target').value.trim().toUpperCase();
        saveRelation(task, type, target, false);
      });
      $('detail-content').querySelectorAll('[data-unlink]').forEach(button => button.addEventListener('click', () => saveRelation(task, button.dataset.unlink, button.dataset.target, true)));
      const copyButton = $('detail-content').querySelector('[data-copy-task]');
      if (copyButton) copyButton.addEventListener('click', () => copyTask(task, copyButton, relationships));
      const index = state.tasks.findIndex((item) => item.id === id);
      if (index >= 0) state.tasks[index] = task;
    } catch (error) {
      if (sequence === state.detailSequence)
        $('detail-content').innerHTML =
          `<h2 id="detail-title">불러오기 실패</h2><p class="banner error">${escape(error.message)}</p>`;
    }
  }

  async function copyTask(task, button, relationships = {items:[]}) {
    const dependencies = task.blocked_by
      .map((id) => {
        const linked = state.tasks.find((item) => item.id === id);
        return `- ${id}${linked ? ` · ${linked.title} (${queues[linked.queue]})` : ' · 기록 확인 필요'}`;
      })
      .join('\n');
    const sources = task.sources
      .map((source) => `- ${source.label}: ${source.url || source.path || ''}`)
      .join('\n');
    const sections = [
      ['페이지 경로', task.page_path || ''],
      ['문제 요약', task.summary],
      ['상세 / 현재 제약', task.detail],
      ['다음 할 일', task.next_action],
      ['완료 기준', task.acceptance],
      ['확인 근거 / 검증 범위', task.verification],
      ['선행 항목', dependencies],
      ['작업 관계', relationships.items.filter(item => item.type !== 'blocked_by').map(item => `- ${relationLabels[item.type]} · ${item.id} · ${item.title} (${queues[item.queue]})`).join('\n')],
      ['처리·결정 내용', task.resolution],
      ['근거 문서·코드·티켓', sources],
    ]
      .filter(([, content]) => content.trim())
      .map(([title, content]) => `## ${title}\n${content.trim()}`);
    const context = [
      task.is_api_request ? 'API 요청 항목' : '',
      `상태: ${queues[task.queue]}`,
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
  function openEditor(id = null, parentId = null) {
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
        'page_path',
        'summary',
        'detail',
        'next_action',
        'acceptance',
        'owner',
        'last_checked',
        'verification',
        'resolution',
        'queue',
        'priority',
        'qa_result',
      ])
        fields.namedItem(key).value = task[key] ?? '';
      for (const key of ['areas', 'blocked_by']) fields.namedItem(key).value = task[key].join(', ');
      fields.namedItem('sources').value = task.sources
        .map((source) => `${source.label} | ${source.url || source.path || ''}`)
        .join('\n');
      for (const [key, value] of Object.entries(task.qa)) fields.namedItem(`qa_${key}`).value = value;
    } else {
      fields.namedItem('queue').value = state.queue || 'todo';
    }
    fields.namedItem('is_api_request').checked = task ? task.is_api_request === true : $('api-only').checked;
    fields.namedItem('parent_id').value = task?.parent_id || parentId || '';
    if (parentId) { fields.namedItem('queue').value = 'todo'; $('editor-title').textContent = parentId+' 하위 작업 만들기'; }
    $('qa-fields').hidden = fields.namedItem('queue').value !== 'qa' && !(task?.category === 'qa' && ['done','archived'].includes(fields.namedItem('queue').value));
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
    if (!task || task.queue !== 'qa') return;
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
          queue: 'done',
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
      'page_path',
      'summary',
      'detail',
      'next_action',
      'acceptance',
      'owner',
      'last_checked',
      'verification',
      'resolution',
      'queue',
      'priority',
      'qa_result',
      'note',
    ])
      payload[key] = form.get(key).trim();
    payload.is_api_request = form.has('is_api_request');
    payload.parent_id = form.get('parent_id').trim().toUpperCase();
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
        task.queue === 'archived'
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
          queue: task.queue === 'archived' ? (task.category === 'qa' ? 'qa' : 'issue') : 'archived',
          note: task.queue === 'archived' ? '보관 해제' : '보관함으로 이동',
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

  setOptions($('edit-queue'), queues);
  setOptions($('edit-qa-result'), qaResults);
  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-queue], [data-open], [data-edit], [data-archive], [data-complete-qa]');
    if (!button) return;
    if (button.dataset.queue) {
      state.mode = 'all';
      $('record-view').value = 'all';
      for (const id of ['search', 'area-filter', 'priority-filter']) $(id).value = '';
      state.queue = button.dataset.queue;
      render();
    } else if (button.dataset.open) openDetail(button.dataset.open);
    else if (button.dataset.completeQa) openQaCompletion(button.dataset.completeQa);
    else if (button.dataset.edit) openEditor(button.dataset.edit);
    else if (button.dataset.archive) archive(button.dataset.archive);
  });
  for (const id of ['search', 'area-filter', 'priority-filter'])
    $(id).addEventListener(id === 'search' ? 'input' : 'change', id === 'search' ? searchChanged : render);
  $('api-only').addEventListener('change', () => {
    state.queue = '';
    render();
  });
  $('reset-filters').addEventListener('click', () => {
    $('api-only').checked = false;
    for (const id of ['search', 'area-filter', 'priority-filter']) $(id).value = '';
    state.mode = 'all';
    state.queue = '';
    $('record-view').value = 'all';
    render();
  });
  $('record-view').addEventListener('change', () => {
    state.mode = $('record-view').value;
    state.queue = '';
    render();
  });
  $('add-task').addEventListener('click', () => openEditor());
  $('refresh').addEventListener('click', () => refresh());
  $('close-detail').addEventListener('click', () => $('detail-panel').close());
  $('detail-panel').addEventListener('close', () => {
    state.detailSequence++;
    state.selectedId = null;
  });
  $('edit-queue').addEventListener('change', () => {
    $('qa-fields').hidden = $('edit-queue').value !== 'qa' && !(state.editing?.category === 'qa' && ['done','archived'].includes($('edit-queue').value));
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
