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
    const metrics = [
      ['todo','grid','착수하기로 정한 작업'],
      ['in_progress','qa','현재 수행 중인 작업'],
      ['issue','issue','조사·처리할 문제'],
      ['qa','qa','검증할 항목'],
      ['waiting','api','결정·외부 조건 대기'],
      ['done','done','완료 기록 보기'],
    ];
    $('metrics').innerHTML = metrics
      .map(([queue, icon, hint]) => {
        const count = scoped.filter((task) => task.queue === queue).length;
        const selected = queue === 'done' ? state.mode === 'done' : state.mode === 'all' && state.queue === queue;
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

  function renderImpactGraph(items, edges, selected, highlighted) {
    if (!items.length) return '<p class="muted">등록된 코드 항목이 없습니다.</p>';
    const labels = new Map(items.map(item => [item.id, item.label]));
    const levels = new Map();
    if (selected) {
      const incoming = new Set(edges.filter(edge => edge.to === selected).map(edge => edge.from));
      for (const item of items) levels.set(item.id, item.id === selected ? 1 : incoming.has(item.id) ? 0 : 2);
    } else {
      const targets = new Set(edges.map(edge => edge.to));
      const pending = items.filter(item => !targets.has(item.id)).map(item => item.id);
      for (const id of pending) levels.set(id, 0);
      const outgoing = new Map(items.map(item => [item.id, []]));
      for (const edge of edges) outgoing.get(edge.from).push(edge.to);
      const walk = (id) => {
        for (const to of outgoing.get(id)) {
          if (levels.has(to)) continue;
          levels.set(to, Math.min(levels.get(id) + 1, 5));
          pending.push(to);
        }
      };
      for (let index = 0; index < pending.length; index++) walk(pending[index]);
      // A component with only cycles has no root. Start it from its first saved node.
      for (const item of items) {
        if (levels.has(item.id)) continue;
        const index = pending.length;
        levels.set(item.id, 0);
        pending.push(item.id);
        for (let offset = index; offset < pending.length; offset++) walk(pending[offset]);
      }
    }

    const cardW = 162, cardH = 48, xGap = 58, yGap = 34, margin = 20;
    const columns = new Map(), points = new Map();
    for (const item of items) {
      const level = levels.get(item.id);
      const row = columns.get(level) || 0;
      points.set(item.id, {x: margin + level * (cardW + xGap), y: margin + row * (cardH + yGap)});
      columns.set(level, row + 1);
    }
    const width = margin * 2 + Math.max(...levels.values()) * (cardW + xGap) + cardW + 20;
    const height = margin * 2 + Math.max(...columns.values()) * (cardH + yGap) - yGap;
    const connections = edges.map(edge => {
      const a = points.get(edge.from), b = points.get(edge.to);
      const y = a.y + cardH / 2, end = b.y + cardH / 2;
      let line;
      if (edge.from === edge.to) {
        line = `M${a.x + cardW} ${y} C${a.x + cardW + 28} ${y - 27} ${a.x + cardW + 28} ${y + 27} ${a.x + cardW} ${y + 10}`;
      } else if (a.x === b.x) {
        const bend = a.x + cardW + 24;
        line = `M${a.x + cardW} ${y} C${bend} ${y} ${bend} ${end} ${b.x + cardW} ${end}`;
      } else {
        const backwards = a.x > b.x;
        const startX = a.x + (backwards ? 0 : cardW), endX = b.x + (backwards ? cardW : 0);
        const bend = (startX + endX) / 2;
        line = `M${startX} ${y} C${bend} ${y} ${bend} ${end} ${endX} ${end}`;
      }
      const label = `${labels.get(edge.from)} → ${labels.get(edge.to)} · ${edge.label}`;
      return `<path class="map-edge" data-from="${escape(edge.from)}" data-to="${escape(edge.to)}" d="${line}" marker-end="url(#task-map-arrow)"><title>${escape(label)}</title></path>`;
    }).join('');
    const cards = items.map(item => {
      const {x, y} = points.get(item.id);
      const parts = [...item.label];
      const limit = /[^\x00-\x7f]/.test(item.label) ? 12 : 20;
      const label = parts.length > limit ? parts.slice(0, limit - 1).join('') + '…' : item.label;
      return `<a href="#task-map-node" data-graph-node="${escape(item.id)}" class="map-node${item.id === selected ? ' selected' : highlighted.has(item.id) ? ' linked' : ''}" aria-label="${escape(item.label)} 기준으로 보기">
        <title>${escape(item.label)} · ${escape(item.kind)}</title><rect x="${x}" y="${y}" width="${cardW}" height="${cardH}" rx="7"/>
        <text x="${x + 9}" y="${y + 18}" class="map-node-type">${escape(item.kind.slice(0, 20))}</text>
        <text x="${x + 9}" y="${y + 35}">${escape(label)}</text></a>`;
    }).join('');
    return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-label="방향별 코드 관계">
      <defs><marker id="task-map-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
        <path d="M0 0 8 4 0 8z" class="map-arrowhead"/></marker></defs>${connections}${cards}</svg>`;
  }

  function renderTaskMap(content, map) {
    const refs = (ref) => ref ? `<code class="monospace">${escape(ref.path)}${ref.line ? ':' + escape(ref.line) : ''}</code>` : '';
    const node = (item) => `<strong>${escape(item.label)}</strong> <span class="muted">${escape(item.kind)}</span>${refs(item.ref)}`;
    const nodes = new Map(map.nodes.map(item => [item.id, item]));
    const evidenceNames = {spec: '기획 기준', code: '코드 확인', browser: '브라우저 실측'};
    const warning = map.stale === true ? '현재 작업본이 저장 기준과 다릅니다. 최신 근거를 다시 확인하세요.'
      : map.stale === null ? '현재 체크아웃의 커밋을 확인할 수 없습니다.' : '';
    content.innerHTML = `
      <p class="muted task-map-revision">기준 커밋 <code class="monospace">${escape(map.revision)}</code></p>
      ${warning ? `<p class="banner error" role="status">${warning}</p>` : ''}
      <section class="task-map-section" aria-labelledby="task-map-impact">
        <h3 id="task-map-impact">코드 영향 범위</h3>
        ${map.nodes.length ? `<label for="task-map-node">기준 항목</label> <select id="task-map-node"><option value="">전체 항목</option>${map.nodes.map(item =>
          `<option value="${escape(item.id)}">${escape(item.label)} · ${escape(item.kind)}</option>`).join('')}</select>` : ''}
        <div class="task-map-focus" id="task-map-focus" aria-live="polite" hidden></div>
        <div class="task-map-graph" id="task-map-graph" role="region" aria-label="코드 영향 그래프" tabindex="0"></div>
        <details class="task-map-details"><summary>관계 표와 파일 근거</summary><div id="task-map-edges" aria-live="polite"></div></details>
      </section>
      <section class="task-map-section" aria-labelledby="task-map-flow">
        <h3 id="task-map-flow">화면 흐름</h3>
        ${map.flows.length ? `<p class="muted">각 행은 따로 기록된 화면 분기입니다.</p>
          <ol class="task-map-flow">${map.flows.map((item, index) => {
            const linked = item.node_ids?.length;
            const tag = linked ? 'button' : 'div';
            return `<li data-map-flow-row="${index}"><${tag} class="task-map-flow-lane"${linked ? ` type="button" data-map-flow="${index}" aria-pressed="false"` : ''}>
              <span class="task-map-flow-node"><small>단계</small><strong>${escape(item.step)}</strong></span>
              <span class="task-map-flow-arrow" aria-hidden="true">→</span>
              <span class="task-map-flow-node"><small>조건</small>${escape(item.condition || '기록 없음')}</span>
              <span class="task-map-flow-arrow" aria-hidden="true">→</span>
              <span class="task-map-flow-node"><small>결과</small>${escape(item.outcome)}</span>
            </${tag}>
            <div class="task-map-flow-evidence"><span class="badge">${escape(evidenceNames[item.kind] ?? item.kind)}</span>
            ${item.evidence ? `<span>${escape(item.evidence)}</span>` : ''}${refs(item.ref)}</div>
            <p class="task-map-flow-links" hidden></p></li>`;
          }).join('')}</ol>`
          : '<p class="muted">등록된 화면 흐름이 없습니다.</p>'}
      </section>`;
    const select = content.querySelector('#task-map-node');
    const edges = content.querySelector('#task-map-edges');
    const graph = content.querySelector('#task-map-graph');
    const focus = content.querySelector('#task-map-focus');
    const lanes = Array.from(content.querySelectorAll('[data-map-flow-row]'));
    let flowIndex = null;

    const draw = () => {
      const selected = select?.value;
      const activeFlow = flowIndex === null ? null : map.flows[flowIndex];
      const matches = new Set();
      const highlighted = new Set(activeFlow?.node_ids ?? []);
      if (selected) {
        map.flows.forEach((flow, index) => {
          if (!flow.node_ids?.includes(selected)) return;
          matches.add(index);
          for (const id of flow.node_ids) if (nodes.get(id).kind === 'test') highlighted.add(id);
        });
        for (const edge of map.edges) {
          const neighbor = edge.from === selected ? edge.to : edge.to === selected ? edge.from : null;
          if (nodes.get(neighbor)?.kind === 'test') highlighted.add(neighbor);
        }
      }
      const direct = activeFlow
        ? map.edges.filter(edge => highlighted.has(edge.from) && highlighted.has(edge.to))
        : map.edges.filter(edge => !selected || edge.from === selected || edge.to === selected);
      const shown = new Set(direct.flatMap(edge => [edge.from, edge.to]));
      for (const id of highlighted) shown.add(id);
      if (selected) shown.add(selected);
      const scoped = selected || activeFlow;
      const visible = scoped ? map.nodes.filter(item => shown.has(item.id)) : map.nodes;
      const rows = selected ? map.edges.filter(edge => shown.has(edge.from) && shown.has(edge.to)
        && (edge.from === selected || edge.to === selected || highlighted.has(edge.from) || highlighted.has(edge.to))) : direct;
      graph.innerHTML = renderImpactGraph(visible, rows, selected, highlighted);
      graph.scrollLeft = 0;
      graph.scrollTop = 0;
      if (select) select.options[0].textContent = activeFlow ? '선택한 분기 항목' : '전체 항목';

      const tests = map.nodes.filter(item => highlighted.has(item.id) && item.kind === 'test');
      focus.hidden = !scoped;
      focus.innerHTML = scoped ? `
        <div class="task-map-focus-heading"><strong>${escape(activeFlow?.step ?? nodes.get(selected).label)}</strong>
          <button type="button" class="text-button" data-map-clear>선택 해제</button></div>
        ${selected ? `<div class="task-map-match-list"><span>관련 화면 분기 ${matches.size}개</span>
          ${Array.from(matches, index => `<button type="button" class="button secondary" data-map-flow="${index}">${escape(map.flows[index].step)}</button>`).join('')}</div>`
          : `<p>연결된 코드 항목 ${highlighted.size}개</p>`}
        <div><span>관련 테스트 ${tests.length}개</span>
          ${tests.length ? `<ul>${tests.map(item => `<li>${node(item)}</li>`).join('')}</ul>` : ''}</div>` : '';

      lanes.forEach((lane, index) => {
        const active = index === flowIndex;
        lane.classList.toggle('linked', matches.has(index) || active);
        lane.querySelector('button')?.setAttribute('aria-pressed', String(active));
        const links = lane.querySelector('.task-map-flow-links');
        links.hidden = !active;
        links.textContent = active
          ? '연결된 코드 · ' + [...highlighted].map(id => nodes.get(id).label).join(' · ') : '';
      });

      const shownEndpoints = new Set(rows.flatMap(edge => [edge.from, edge.to]));
      const isolated = visible.filter(item => !shownEndpoints.has(item.id));
      edges.innerHTML = (rows.length ? `
        <table class="task-map-table">
          <thead><tr><th scope="col">출발</th><th scope="col">관계 →</th><th scope="col">대상</th></tr></thead>
          <tbody>${rows.map(edge => `<tr><td>${node(nodes.get(edge.from))}</td><td>${escape(edge.label)}</td><td>${node(nodes.get(edge.to))}</td></tr>`).join('')}</tbody>
        </table>` : `<p class="muted">${map.nodes.length ? '연결된 코드 관계가 없습니다.' : '등록된 코드 영향 범위가 없습니다.'}</p>`) +
        (isolated.length ? `<ul class="task-map-flow">${isolated.map(item => `<li>${node(item)}</li>`).join('')}</ul>` : '');
    };

    select?.addEventListener('change', () => { flowIndex = null; draw(); });
    content.addEventListener('click', event => {
      const button = event.target.closest('[data-map-flow]');
      if (button && select) {
        const index = Number(button.dataset.mapFlow);
        flowIndex = flowIndex === index ? null : index;
        select.value = '';
        draw();
        if (!button.isConnected) select.focus({preventScroll: true});
      } else if (event.target.closest('[data-map-clear]') && select) {
        select.value = '';
        flowIndex = null;
        draw();
        select.focus({preventScroll: true});
      }
    });
    graph.addEventListener('click', event => {
      const link = event.target.closest('[data-graph-node]');
      if (!link || !select) return;
      event.preventDefault();
      select.value = link.dataset.graphNode;
      flowIndex = null;
      draw();
      select.focus({preventScroll: true});
    });
    draw();
  }

  function mountTaskMap(id, sequence) {
    $('detail-content').querySelector('.detail-page-path').insertAdjacentHTML('afterend', `
      <details class="detail-section task-map">
        <summary>코드 영향 범위 · 화면 흐름</summary>
        <div class="task-map-content"></div>
      </details>`);
    const details = $('detail-content').querySelector('.task-map');
    const content = details.querySelector('.task-map-content');
    let loading = false;
    let loaded = false;
    async function load() {
      if (loading || loaded || sequence !== state.detailSequence) return;
      loading = true;
      content.innerHTML = '<p class="muted" role="status">코드 영향 범위와 화면 흐름을 불러오는 중…</p>';
      try {
        const {map} = await request(`/api/tasks/${encodeURIComponent(id)}/map`);
        if (sequence !== state.detailSequence) return;
        if (map) renderTaskMap(content, map);
        else content.innerHTML = '<p class="muted">등록된 코드 영향 범위와 화면 흐름이 없습니다.</p>';
        loaded = true;
      } catch (error) {
        if (sequence !== state.detailSequence) return;
        content.innerHTML = `<p class="banner error" role="alert">불러오기 실패 · ${escape(error.message)}</p>
          <button type="button" class="button secondary">다시 불러오기</button>`;
        content.querySelector('button').addEventListener('click', load);
      } finally {
        loading = false;
      }
    }
    details.addEventListener('toggle', () => {
      if (details.open) load();
    });
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
    $('copy-detail-status').textContent = '';
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
      mountTaskMap(id, sequence);
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

  function renderSourceMarkdown(text) {
    const lines = text.split(/\r?\n/);
    const fragment = document.createDocumentFragment();
    const heading = /^(#{1,6})\s+(.+)$/;
    const fence = /^(`{3,}|~{3,})/;
    const listItem = /^ {0,3}(?:([-+*])|(\d{1,9})[.)])\s+(.+)$/;
    const cells = line => line.trim().replace(/^\||(?<!\\)\|$/g, '').split(/(?<!\\)\|/).map(value => value.trim().replace(/\\\|/g, '|'));
    const tableAt = i => lines[i].includes('|') && lines[i + 1]?.includes('|') &&
      cells(lines[i + 1]).every(cell => /^:?-{3,}:?$/.test(cell)) && cells(lines[i]).length === cells(lines[i + 1]).length;

    // Document markup stays text. Only recognized Markdown syntax creates DOM nodes.
    function appendInline(parent, value) {
      const tokens = /`([^`]+)`|\*\*([^*]+)\*\*|\*([^*]+)\*|(?<!!)\[([^\[\]]+)\]\((https?:\/\/[^\s)]+)\)/gi;
      let end = 0;
      for (const match of value.matchAll(tokens)) {
        parent.append(value.slice(end, match.index));
        const item = document.createElement(match[1] ? 'code' : match[2] ? 'strong' : match[3] ? 'em' : 'a');
        item.textContent = match[1] ?? match[2] ?? match[3] ?? match[4];
        if (match[5]) {
          item.href = match[5];
          item.target = '_blank';
          item.rel = 'noopener noreferrer';
        }
        parent.append(item);
        end = match.index + match[0].length;
      }
      parent.append(value.slice(end));
    }

    function appendBlock(parent, tag, content) {
      const element = document.createElement(tag);
      if (content) appendInline(element, content);
      parent.append(element);
      return element;
    }

    let i = 0;
    while (i < lines.length) {
      const line = lines[i++];
      if (!line.trim()) continue;
      const title = line.match(heading);
      const opening = line.match(fence);
      const entry = line.match(listItem);
      if (title) {
        appendBlock(fragment, 'h' + title[1].length, title[2]);
      } else if (opening) {
        const closing = new RegExp(`^${opening[1][0]}{${opening[1].length},}\\s*$`);
        const block = [];
        while (i < lines.length && !closing.test(lines[i])) block.push(lines[i++]);
        if (i < lines.length) i++;
        const pre = appendBlock(fragment, 'pre');
        appendBlock(pre, 'code').textContent = block.join('\n');
      } else if (entry) {
        const ordered = Boolean(entry[2]);
        const list = appendBlock(fragment, ordered ? 'ol' : 'ul');
        if (ordered) list.start = Number(entry[2]);
        let item = entry;
        while (item && Boolean(item[2]) === ordered) {
          appendBlock(list, 'li', item[3]);
          item = lines[i]?.match(listItem);
          if (!item || Boolean(item[2]) !== ordered) break;
          i++;
        }
      } else if (tableAt(i - 1)) {
        const wrapper = appendBlock(fragment, 'div');
        wrapper.className = 'source-table';
        const table = appendBlock(wrapper, 'table');
        const headers = cells(line);
        const head = table.createTHead().insertRow();
        for (const value of headers) appendBlock(head, 'th', value).scope = 'col';
        const body = table.createTBody();
        i++;
        while (lines[i]?.includes('|') && !heading.test(lines[i]) && !fence.test(lines[i]) && !listItem.test(lines[i])) {
          const row = body.insertRow();
          const values = cells(lines[i++]);
          for (let column = 0; column < headers.length; column++) appendBlock(row, 'td', values[column]);
        }
      } else {
        const paragraph = [line];
        while (lines[i]?.trim() && !heading.test(lines[i]) && !fence.test(lines[i]) && !listItem.test(lines[i]) && !tableAt(i)) {
          paragraph.push(lines[i++]);
        }
        appendBlock(fragment, 'p', paragraph.join(' '));
      }
    }
    return fragment;
  }

  let sourceSequence = 0;
  async function openSource(source) {
    const sequence = ++sourceSequence;
    const dialog = $('source-dialog');
    const body = $('source-body');
    const query = `path=${encodeURIComponent(source.path)}`;
    $('source-title').textContent = source.path;
    body.textContent = '문서를 불러오는 중입니다.';
    body.setAttribute('aria-busy', 'true');
    body.scrollTop = 0;
    if (!dialog.open) dialog.showModal();
    try {
      const {format, text} = await request(`/api/source?${query}`);
      if (!dialog.open || sequence !== sourceSequence) return;
      if (format === 'html') {
        const frame = document.createElement('iframe');
        const preview = `/source?${query}&preview=1`;
        frame.title = source.label || source.path;
        frame.setAttribute('sandbox', '');
        frame.setAttribute('referrerpolicy', 'no-referrer');
        frame.src = window.workUrl ? window.workUrl(preview) : preview;
        body.replaceChildren(frame);
      } else if (format === 'markdown') {
        body.replaceChildren(renderSourceMarkdown(text));
      } else {
        const raw = document.createElement('pre');
        raw.textContent = text;
        body.replaceChildren(raw);
      }
    } catch (error) {
      if (dialog.open && sequence === sourceSequence) body.textContent = error.message;
    } finally {
      if (sequence === sourceSequence) body.setAttribute('aria-busy', 'false');
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
      const queue = button.dataset.queue;
      state.mode = queue === 'done' ? 'done' : 'all';
      $('record-view').value = state.mode;
      for (const id of ['search', 'area-filter', 'priority-filter']) $(id).value = '';
      state.queue = queue === 'done' ? '' : queue;
      render();
      if (event.detail === 0) $('metrics').querySelector(`[data-queue="${queue}"]`)?.focus({preventScroll: true});
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
  let worktreeRequest = 0;
  let listedWorktrees = [];
  let pendingWorktree = null;
  let removingWorktree = false;
  const worktreeFeedback = $('worktree-feedback');
  const worktreeConfirmation = $('worktree-confirmation');

  function worktreeMessage(message, confirm = false, ignoredPaths = [], newPaths = []) {
    worktreeFeedback.hidden = !message;
    $('worktree-message').textContent = message;
    worktreeConfirmation.hidden = !confirm;
    const ignoredList = $('worktree-ignored-paths');
    ignoredList.hidden = !ignoredPaths.length;
    const added = new Set(newPaths);
    ignoredList.replaceChildren();
    for (const path of ignoredPaths) {
      const item = document.createElement('li');
      item.textContent = added.has(path) ? '새로 발견 · ' + path : path;
      ignoredList.append(item);
    }
  }

  function worktreeActivity(item) {
    const value = item.last_session_at || item.last_git_at;
    if (!value || Number.isNaN(Date.parse(value))) return '최근 사용 기록 없음';
    const formatted = new Date(value).toLocaleString('ko-KR', {
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    });
    return `${item.last_session_at ? '마지막 세션 기록' : '최근 Git 활동'} <time datetime="${escape(value)}">${escape(formatted)}</time>`;
  }

  async function loadWorktrees() {
    const sequence = ++worktreeRequest;
    const list = $('worktree-list');
    listedWorktrees = [];
    list.textContent = '워크트리를 불러오는 중…';
    try {
      const {worktrees} = await request('/api/worktrees');
      if (sequence !== worktreeRequest) return;
      listedWorktrees = worktrees;
      list.innerHTML = worktrees.length ? worktrees.map((item, index) => `
        <article><strong>${escape(item.branch || item.head || '브랜치 없음')}</strong>
          ${item.current ? '<span class="badge">현재 위치</span>' : ''}
          ${item.active ? badge('in_progress', queues) : ''}
          <code>${escape(item.path)}</code>
          <div class="muted">${worktreeActivity(item)}</div>
          <div class="worktree-actions"><span class="muted">미커밋 ${item.uncommitted ?? '확인 불가'} · 미푸시 ${item.unpushed ?? '추적 정보 없음'}${item.ignored_paths?.length ? ' · Git 무시 파일 있음' : ''}</span>
            <button type="button" class="button secondary" data-remove-worktree="${index}">${item.removable ? (item.backup_branch ? '브랜치 보관 후 삭제' : (item.ignored_paths?.length ? '파일 확인 후 삭제' : '삭제')) : '삭제 불가 이유'}</button>
          </div>
        </article>`).join('') : '표시할 Git 워크트리가 없습니다.';
    } catch (error) {
      if (sequence === worktreeRequest) list.textContent = error.message;
    }
  }

  $('open-worktrees').addEventListener('click', () => {
    pendingWorktree = null;
    worktreeMessage(removingWorktree ? '워크트리 삭제 중… 큰 폴더는 시간이 걸릴 수 있습니다.' : '');
    $('worktree-dialog').showModal();
    loadWorktrees();
  });
  $('worktree-list').addEventListener('click', event => {
    const button = event.target.closest('[data-remove-worktree]');
    if (!button || removingWorktree) return;
    reviewWorktree(listedWorktrees[Number(button.dataset.removeWorktree)]);
  });

  function reviewWorktree(item, notice = '', newPaths = []) {
    $('worktree-dialog').scrollTop = 0;
    const ignoredPaths = item.ignored_paths ?? [];
    pendingWorktree = item.removable ? item : null;
    if (!pendingWorktree) {
      const ignoredMessage = ignoredPaths.length ? '\nGit이 무시하는 파일 및 폴더' : '';
      worktreeMessage(notice + '삭제할 수 없습니다. ' + item.remove_reasons.join(' ') + ignoredMessage, false, ignoredPaths, newPaths);
      return;
    }
    const warning = ignoredPaths.length ? '\n아래 Git 무시 파일과 폴더도 함께 삭제됩니다.' : '';
    let preservation = '브랜치는 유지됩니다.';
    if (item.backup_branch) preservation = `커밋 ${item.head}를 보관용 브랜치에 저장한 뒤 폴더를 삭제합니다.\n${item.backup_branch}`;
    else if (item.retained_by) preservation = `커밋 ${item.head}는 ${item.retained_by} 브랜치에 포함돼 있습니다.`;
    $('confirm-worktree-remove').textContent = item.backup_branch ? '브랜치 보관 후 삭제' : '폴더 삭제';
    worktreeMessage(notice + `워크트리 폴더를 삭제할까요?\n${item.path}\n${preservation}${warning}`, true, ignoredPaths, newPaths);
    $('cancel-worktree-remove').focus({preventScroll: true});
  }
  $('cancel-worktree-remove').addEventListener('click', () => {
    pendingWorktree = null;
    worktreeMessage('삭제를 취소했습니다.');
  });
  $('confirm-worktree-remove').addEventListener('click', async () => {
    if (!pendingWorktree || removingWorktree) return;
    const {path, commit, backup_branch, ignored_paths = []} = pendingWorktree;
    pendingWorktree = null;
    removingWorktree = true;
    worktreeMessage('워크트리 삭제 중… 큰 폴더는 시간이 걸릴 수 있습니다.');
    try {
      const result = await request('/api/worktrees/remove', {method: 'POST', body: JSON.stringify({path, commit, backup_branch, ignored_paths})});
      const preserved = result.backup_branch ? `\n보관용 브랜치 ${result.backup_branch}` : '';
      worktreeMessage('워크트리 폴더를 삭제했습니다.' + preserved);
      if ($('worktree-dialog').open) await loadWorktrees();
    } catch (error) {
      worktreeMessage('삭제 실패. ' + error.message);
      if ($('worktree-dialog').open) {
        await loadWorktrees();
        const fresh = listedWorktrees.find(item => item.path === path);
        const confirmed = new Set(ignored_paths);
        const added = fresh?.ignored_paths?.filter(value => !confirmed.has(value)) ?? [];
        if (fresh && added.length) {
          reviewWorktree(fresh, `새로 발견된 항목 ${added.length}개를 포함해 아래 목록을 다시 확인해주세요.\n`, added);
        }
      }
    } finally {
      removingWorktree = false;
    }
  });
  $('copy-detail-id').addEventListener('click', async () => {
    const sequence = state.detailSequence;
    const id = state.selectedId;
    if (!id) return;
    const status = $('copy-detail-status');
    status.textContent = '';
    try {
      await navigator.clipboard.writeText(id);
      if (sequence === state.detailSequence) status.textContent = '복사됨';
    } catch {
      if (sequence === state.detailSequence) status.textContent = '복사 실패';
    }
  });
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
  $('source-dialog').addEventListener('close', () => {
    if ($('source-dialog').open) return;
    sourceSequence++;
    $('source-body').replaceChildren();
    $('source-body').setAttribute('aria-busy', 'false');
  });
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
  refresh(false).then(() => {
    const ticket = new URLSearchParams(location.search).get('ticket');
    if (ticket && state.tasks.some(task => task.id === ticket)) openDetail(ticket);
  });
})();
