(() => {
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
  const query = new URLSearchParams(location.search); const all = query.get('all') === '1';
  const labels = { ready: '계획 승인 대기', running: '실행 중', verifying: '테스트 중', done: '완료', failed: '확인 필요', paused: '중단', needs_recheck: '재확인 필요', waiting_answer: '사용자 답변 대기', succeeded: '성공', interrupted: '중단', todo: '대기', in_progress: '진행 중' };
  const roleTitles = { plan: '기획', implement: '구현', verify: '검토' };
  let channels = []; let room; let selected = query.get('goal') || ''; let busy = false; let roomSequence = 0; let channelSignature = ''; let feedSignature = ''; let settingsDraft; let settingsProject; let settingsLoadedProject; let settingsSequence = 0; let settingsProjects = []; let roleCounter = 0; let crewsetCatalog; let createProject; let clientDraft; const drafts = new Map();
  const time = s => s ? new Date(s).toLocaleString('ko-KR', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
  const showError = s => { $('goal-error').textContent = s; $('goal-error').hidden = !s; };
  const notify = s => { $('goal-notice').textContent = s; $('goal-notice').hidden = !s; };
  const open = dialog => dialog.showModal ? dialog.showModal() : dialog.setAttribute('open', '');
  const close = dialog => dialog.close ? dialog.close() : dialog.removeAttribute('open');
  let renderedGoal = '';
  function resizeComposer() {
    const input = $('message-body'); input.style.height = 'auto';
    const minimum = parseFloat(getComputedStyle(input).minHeight) || 36;
    const height = Math.min(120, Math.max(minimum, input.scrollHeight));
    input.style.height = `${height}px`; input.style.overflowY = input.scrollHeight > height ? 'auto' : 'hidden';
  }
  function closeTeam() {
    if (!$('team-dialog').open) return;
    close($('team-dialog')); $('open-team').setAttribute('aria-expanded','false'); $('open-team').focus();
  }
  $('open-team').addEventListener('click', () => {
    if (!room || room.goal.id !== selected) return;
    open($('team-dialog')); $('open-team').setAttribute('aria-expanded','true');
  });
  $('close-team').addEventListener('click', closeTeam);
  $('team-dialog').addEventListener('cancel', event => { event.preventDefault(); closeTeam(); });
  $('team-dialog').addEventListener('close', () => $('open-team').setAttribute('aria-expanded','false'));
  $('message-body').addEventListener('input', resizeComposer);
  window.addEventListener('resize', resizeComposer);
  function project() { return channels.find(g => g.id === selected)?.project_id || query.get('project') || undefined; }
  function projectName() { return channels.find(g => g.id === selected)?.project_name || $('current-project').textContent; }
  async function api(path, body, projectId = project(), method = 'POST') {
    const url = new URL(window.workUrl(path), location.origin); if (projectId) url.searchParams.set('project', projectId);
    const response = await fetch(url.pathname + url.search, body === undefined ? undefined : { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await response.json(); if (!response.ok) throw new Error(data.error || '요청을 처리하지 못했습니다.'); return data;
  }
  function name(role) { return role === 'user' ? '나' : role === 'tack' ? 'Tack' : room?.settings.roles[role]?.name || roleTitles[role] || role; }
  function badge(status) { return el('span', labels[status] || status, `goal-badge ${status}`); }
  function renderChannels() {
    const signature = JSON.stringify(channels.map(g => [g.id, g.status, g.title, g.version, g.planning, g.pending_request, g.worker, g.project_name]));
    if (signature === channelSignature) { for (const button of $('channels').querySelectorAll('button')) button.classList.toggle('selected', button.dataset.goal === selected); return; }
    channelSignature = signature; const list = $('channels'); list.replaceChildren();
    for (const goal of channels) {
      const button = el('button', undefined, `channel ${goal.id === selected ? 'selected' : ''}`); button.type = 'button'; button.dataset.goal = goal.id;
      const top = el('span', goal.project_name || '', 'channel-project'); const title = el('strong', goal.title); title.title = goal.title; const meta = el('span', goal.pending_request && !goal.worker ? '요청 전달 중' : goal.planning && goal.status === 'running' ? '계획 생성 중' : labels[goal.status] || goal.status, 'channel-state');
      button.append(top, title, meta); button.addEventListener('click', () => selectGoal(goal.id)); list.append(button);
    }
    if (!channels.length) list.append(el('p', '아직 목표가 없습니다. 새 목표를 만들어 팀의 작업을 시작하세요.', 'channel-empty'));
  }
  async function selectGoal(id) {
    drafts.set(selected, { body: $('message-body').value, kind: $('message-kind').value, target: $('message-target').value, optionsOpen: $('composer-settings').open, clientDraft });
    const draft = drafts.get(id); $('message-body').value = draft?.body || ''; $('message-kind').value = draft?.kind || 'note'; $('message-target').value = draft?.target || ''; clientDraft = draft?.clientDraft;
    closeTeam(); $('goal-overview').open = false; $('composer-settings').open = draft?.optionsOpen || false; $('open-team').disabled = true;
    selected = id; room = null; $('run-action').disabled = true; $('send-message').disabled = true; $('message-form').hidden = true; $('room-title').textContent = '목표 불러오는 중…'; feedSignature = ''; delete $('chat-feed').dataset.loaded; const url = new URL(location.href); url.searchParams.set('goal', id); history.replaceState(null, '', url); renderChannels(); await loadRoom(); if (draft) { $('message-target').value = draft.target; updateComposer(); }
  }
  function detailContent(message) {
    const data = message.data || {}; const box = el('div', undefined, 'message-detail');
    const run = room.runs.find(r => r.id === data.run_id);
    const step = room.goal.steps.find(s => s.task_id === (data.task_id || run?.task_id));
    if (step) { box.append(el('strong', step.title), el('p', step.prompt)); if (step.files?.length) box.append(el('code', `대상 파일: ${step.files.join(', ')}`)); }
    if (data.steps) for (const item of data.steps) { box.append(el('strong', `${name(item.role)} · ${item.title}`), el('p', item.prompt)); if (item.files?.length) box.append(el('code', item.files.join(', '))); }
    const checks = data.verification || (data.check ? [data.check] : []);
    for (const check of checks) box.append(el('code', Array.isArray(check) ? check.join(' ') : `${check.argv.join(' ')} → ${labels[check.status] || check.status} · exit ${check.exit_code}`));
    if (data.recommendation) box.append(el('p', data.recommendation));
    if (data.roles) for (const [id, role] of Object.entries(data.roles)) box.append(el('p', `${role.name} (@${id}) · ${role.kind || id} · 스킬: ${(role.skills || []).join(', ') || '없음'}${role.instructions ? '\n' + role.instructions : ''}`));
    if (data.acceptance) box.append(el('p', `완료 조건: ${data.acceptance}`));
    if (data.files?.length && !step) box.append(el('code', data.files.join(', ')));
    if (data.reason || data.error || run?.error) box.append(el('p', `상태 근거: ${data.reason || data.error || run.error}`));
    if (run) box.append(el('p', `실행 ${run.id} · ${labels[run.status] || run.status} · 처리 토큰 ${(run.tokens || 0).toLocaleString()} · ${run.model || 'Codex 기본 모델 설정'}`, 'technical'));
    return box;
  }
  function renderRoom() {
    const goal = room.goal; const changedGoal = renderedGoal !== goal.id; const teamScroll = $('team-content').scrollTop;
    if (changedGoal) { closeTeam(); $('goal-overview').open = false; renderedGoal = goal.id; }
    $('open-team').disabled = false; $('room-title').title = goal.title; $('room-request').textContent = goal.title;
    $('room-project').textContent = projectName(); $('room-title').textContent = goal.title; $('room-subtitle').textContent = goal.acceptance;
    $('room-status').className = `goal-badge ${goal.status}`; $('room-status').textContent = goal.pending_request && !goal.worker ? '요청 전달 중' : goal.pending_request?.kind === 'question' && goal.worker ? '답변 중' : goal.planning && goal.status === 'running' ? '계획 생성 중' : labels[goal.status] || goal.status;
    const reasons = { token_limit: '처리 토큰 한도로 중단했습니다. 설정을 확인한 뒤 재개할 수 있습니다.', time_limit: '시간 한도로 중단했습니다. 설정을 확인한 뒤 재개할 수 있습니다.', round_limit: '자동 수정 횟수 한도에 도달했습니다. 결과를 확인해주세요.', scope_review_required: '승인 범위를 벗어난 요청입니다. 사용자 결정이 필요합니다.', server_overloaded: '모델 서버가 혼잡해 실행을 마치지 못했습니다. 잠시 후 명시적으로 재개해주세요.', interrupted: '실행을 중단했습니다. 실제 결과를 확인한 뒤 재개하세요.' };
    $('room-reason').hidden = !goal.error && goal.status !== 'needs_recheck'; $('room-reason').textContent = goal.status === 'needs_recheck' && goal.worker ? '실행 관리자의 응답이 없습니다. 기존 실행 종료를 확인한 뒤 CLI recover로 복구해주세요.' : reasons[goal.error] || '작업 결과·근거에서 실행 상태를 확인해주세요.';
    const policy = goal.worker ? goal.execution_settings : room.settings; $('room-mode').textContent = `${goal.pending_request?.kind === 'question' && goal.worker ? '읽기 전용 질문' : policy?.autopilot ? 'Autopilot' : '수동'}${goal.worker ? ' · 실행 중' : ' · 다음 실행'}`;
    const action = $('run-action'); const running = ['running', 'verifying'].includes(goal.status);
    action.hidden = !running && (!!goal.worker || !['ready', 'paused', 'failed'].includes(goal.status)); action.disabled = !room.execution_enabled || !!goal.pause_requested;
    action.textContent = running ? goal.pause_requested ? '중단 요청 중' : '실행 중단' : goal.pending_request ? '대기 요청 실행' : goal.planning ? '계획 생성' : goal.status === 'ready' ? '계획 승인·실행' : goal.status === 'failed' && ['verify','assess'].includes(room.settings.roles[goal.steps[goal.next_step]?.role]?.kind || goal.steps[goal.next_step]?.role) ? '수정 후 재검증' : '작업 재개';
    const signature = JSON.stringify([room.messages, goal.status, room.settings.version]);
    if (signature !== feedSignature) {
      feedSignature = signature; const feed = $('chat-feed'); const atBottom = feed.scrollHeight - feed.clientHeight - feed.scrollTop < 90; const previousScroll = feed.scrollTop;
      const expanded = new Set(Array.from(feed.querySelectorAll('details[open]')).map(d => d.dataset.message)); feed.replaceChildren();
      for (const message of room.messages) {
        const article = el('article', undefined, `chat-message ${message.sender === 'user' ? 'from-user' : ''} ${message.kind === 'decision' || message.kind === 'plan' ? 'decision-message' : ''}`);
        const avatar = el('div', message.sender === 'tack' ? 'T' : name(message.sender).slice(0, 1), `avatar role-${message.sender}`);
        const content = el('div', undefined, 'message-content'); const head = el('div', undefined, 'message-heading');
        head.append(el('strong', name(message.sender)), el('time', time(message.created_at)));
        if (message.target) head.append(el('span', `→ @${name(message.target)}`, 'mention'));
        const kinds = { note: '메모', question: '질문', revision: '수정 요청', review: '검토 요청', handoff: '작업 전달', result: '실제 결과', answer: '답변', decision: message.data?.reason ? '사용자 확인' : '결정·상태', plan: '계획 검토', verification: '실행 검증', request: '에이전트 요청', event: '실행 기록', goal: '목표' };
        head.append(el('span', kinds[message.kind] || '기록', 'message-kind')); content.append(head, el('p', message.body, 'message-body'));
        const details = el('details', undefined, 'message-details'); details.dataset.message = message.id; details.open = expanded.has(message.id); details.append(el('summary', message.kind === 'plan' ? '작업 계획 상세 보기' : '작업 결과·근거 보기'), detailContent(message)); content.append(details); article.append(avatar, content); feed.append(article);
      }
      if (!room.messages.length) feed.append(el('p', '아직 대화가 없습니다. 작업 결과와 전달 요청을 여기에 표시합니다.', 'chat-empty'));
      feed.scrollTop = atBottom || feed.dataset.loaded !== goal.id ? feed.scrollHeight : previousScroll; feed.dataset.loaded = goal.id;
    }
    const members = $('team-members'); members.replaceChildren();
    for (const role of Object.keys(room.settings.roles)) {
      const config = room.settings.roles[role]; const runs = room.runs.filter(r => r.role === role); const last = runs.at(-1); const member = el('div', undefined, 'team-member');
      member.append(el('span', config.name.slice(0, 1), `avatar role-${role}`)); const info = el('div'); info.append(el('strong', config.name), el('small', last?.status === 'running' ? '작업 중' : last ? labels[last.status] || last.status : '대기')); member.append(info); members.append(member);
    }
    const steps = $('team-steps'); steps.replaceChildren();
    goal.steps.forEach(step => { const row = el('li'); row.append(el('span', step.task_status === 'done' ? '✓' : '○', 'step-mark'), el('span', step.title), badge(step.task_status || 'todo')); steps.append(row); });
    const budget = $('budget-state'); budget.replaceChildren(); const limits = goal.execution_settings || room.settings;
    budget.append(el('p', `자동 수정 ${goal.auto_rounds || 0} / ${limits.max_rounds}회`), el('p', `처리 토큰 ${(goal.token_used || 0).toLocaleString()} / ${limits.max_tokens.toLocaleString()}`), el('p', `시간 한도 ${limits.max_minutes}분`), el('small', goal.worker ? '현재 실행의 설정' : goal.execution_settings ? '최근 실행 기준 · 설정 변경은 다음 실행부터' : '다음 실행에 적용'));
    $('connection-status').textContent = `${room.runs.length}개 실행 · ${room.messages.length}개 작업 기록. 채팅 표시를 위한 별도 요약 호출은 없습니다.`;
    $('team-content').scrollTop = changedGoal ? 0 : teamScroll;
    updateComposer(); if (changedGoal) resizeComposer();
  }
  async function loadRoom() {
    if (!selected) { closeTeam(); $('open-team').disabled = true; room = null; $('message-form').hidden = true; $('run-action').hidden = true; return; }
    const sequence = ++roomSequence; const id = selected; const projectId = project();
    try { const data = await api(`/api/goals/${id}/chat`, undefined, projectId); if (id !== selected || sequence !== roomSequence) return; room = data; $('message-form').hidden = false; renderRoom(); }
    catch (e) { if (sequence === roomSequence) showError(e.message); }
  }
  async function refresh() {
    if (busy) return; busy = true;
    try {
      const data = await api(all ? '/api/orchestration?all=1' : '/api/orchestration', undefined, query.get('project') || undefined);
      channels = data.goals.map(g => ({ ...g, project_id: g.project_id || data.project?.id, project_name: g.project_name || data.project?.name }));
      if (!channels.some(g => g.id === selected)) { selected = channels[0]?.id || ''; feedSignature = ''; }
      $('scope-description').textContent = all ? '전체 프로젝트의 목표' : `${data.project?.name || '현재 프로젝트'}의 목표`;
      $('scope-toggle').href = window.workUrl(all ? '/goals' : '/goals?all=1'); $('scope-toggle').textContent = all ? '현재 프로젝트만' : '전체 목표';
      $('new-goal').disabled = !data.execution_enabled; if (!data.execution_enabled) $('connection-status').textContent = '조회 모드입니다. 실행이 허용된 Dashboard에서 작업을 요청할 수 있습니다.'; renderChannels(); await loadRoom();
    } catch (e) { showError(e.message); } finally { busy = false; }
  }
  function updateComposer() {
    if (!room || room.goal.id !== selected) { $('send-message').disabled = true; return; } const select = $('message-target'); const previous = select.value; select.replaceChildren(new Option('대상 없음', ''));
    for (const role of Object.keys(room.settings.roles)) select.append(new Option(`@${name(role)}`, role)); select.value = previous;
    const kind = $('message-kind').value;
    const allowed = kind === 'revision' ? ['implement','plan'] : kind === 'review' ? ['verify','assess'] : null;
    if (allowed && !allowed.includes(room.settings.roles[select.value]?.kind || select.value)) select.value = Object.keys(room.settings.roles).find(r => allowed.includes(room.settings.roles[r].kind || r)) || '';
    if (kind === 'answer') select.value = room.goal.steps[room.goal.next_step]?.role || '';
    for (const option of select.options) option.disabled = !!allowed && !!option.value && !allowed.includes(room.settings.roles[option.value]?.kind || option.value);
    select.disabled = kind === 'answer';
    const texts = { answer: ['답변 전달·재개 ↑', '사용자 답변을 반영해 기획 또는 대기 중인 작업을 이어갑니다.'], note: ['메모 저장 ↑', '메모는 모델을 호출하지 않습니다.'], question: ['질문 전달 ↑', '선택한 역할이 읽기 전용으로 답변합니다.'], revision: ['수정 요청 실행 ↑', '승인된 범위에서 선택한 역할부터 후속 작업을 다시 수행합니다.'], review: ['검토 요청 실행 ↑', '현재 코드를 검토합니다. Autopilot이면 범위 안의 수정을 이어갈 수 있습니다.'] };
    const purposes = { note:'메모', answer:'답변 · 작업 이어가기', question:'질문', revision:'수정 요청', review:'검토 요청' };
    $('composer-summary').textContent = `${purposes[kind]} · ${select.value ? '@' + name(select.value) : '대상 없음'}`;
    $('send-message').textContent = texts[kind][0]; $('composer-hint').textContent = room.goal.worker && kind !== 'note' ? '실행 중입니다. 새 실행 요청은 종료 후 전달할 수 있습니다.' : texts[kind][1];
    $('send-message').disabled = kind !== 'note' && (!room.execution_enabled || !!room.goal.worker || !!room.goal.pending_request || kind === 'answer' && room.goal.status !== 'waiting_answer');
  }
  $('message-kind').addEventListener('change', updateComposer);
  $('message-target').addEventListener('change', updateComposer);
  $('message-form').addEventListener('submit', async event => {
    event.preventDefault(); if (!room || room.goal.id !== selected) return;
    const body = $('message-body').value; const kind = $('message-kind').value; let target = $('message-target').value || null;
    const mention = /^@([^\s]+)/.exec(body); if (mention) { const found = Object.keys(room.settings.roles).find(r => mention[1] === r || mention[1] === name(r)); if (!found) { showError('멘션할 역할을 찾을 수 없습니다.'); return; } if (found) { if (target && target !== found) { showError('멘션과 전달 대상이 다릅니다.'); return; } target = found; } }
    const signature = JSON.stringify([body, kind, target]); if (!clientDraft || clientDraft.signature !== signature) clientDraft = { signature, id: crypto.randomUUID() };
    const id = selected; const projectId = project(); $('send-message').disabled = true; $('request-state').textContent = '전달 중…';
    try {
      await api(`/api/goals/${id}/messages`, { version: room.goal.version, body, kind, target, client_id: clientDraft.id }, projectId);
      if (selected === id && $('message-body').value === body) { $('message-body').value = ''; resizeComposer(); } clientDraft = null; showError(''); $('request-state').textContent = kind === 'note' ? '메모를 저장했습니다.' : '요청을 전달했습니다. 실제 진행 상태를 확인하세요.'; await refresh();
    } catch (e) { showError(e.message); $('request-state').textContent = '입력은 보존했습니다.'; await loadRoom(); } finally { if (room) updateComposer(); }
  });
  $('run-action').addEventListener('click', async () => {
    if (!room || room.goal.id !== selected) return; const action = ['running','verifying'].includes(room.goal.status) ? 'pause' : 'start'; const goal = room.goal; $('run-action').disabled = true;
    try { await api(`/api/goals/${goal.id}/${action}`, { version: goal.version }); showError(''); notify(action === 'pause' ? '중단을 요청했습니다.' : '실행을 요청했습니다.'); await refresh(); }
    catch (e) { showError(e.message); await loadRoom(); }
  });
  $('refresh').addEventListener('click', refresh);
  $('new-goal').addEventListener('click', () => { createProject = project(); $('goal-project-hint').textContent = `${projectName()}에서 계획을 생성합니다.`; $('form-error').hidden = true; open($('goal-dialog')); });
  $('close-goal').addEventListener('click', () => close($('goal-dialog'))); $('cancel-goal').addEventListener('click', () => close($('goal-dialog')));
  $('goal-form').addEventListener('submit', async event => {
    event.preventDefault(); const form = event.currentTarget; $('submit-goal').disabled = true;
    try { const goal = await api('/api/goals/draft', { title: form.elements.title.value, acceptance: form.elements.acceptance.value, verification_text: form.elements.verification_text.value }, createProject); selected = goal.id; feedSignature = ''; close($('goal-dialog')); notify('읽기 전용으로 계획을 생성하고 있습니다. 계획 확인 후 승인하면 구현을 시작합니다.'); await refresh(); }
    catch (e) { $('form-error').textContent = e.message; $('form-error').hidden = false; } finally { $('submit-goal').disabled = false; }
  });
  const kindNames = { plan: '기획 · 읽기 전용', implement: '구현 · 파일 수정', assess: '평가 · 읽기 전용', verify: '검증 · 읽기 전용' };
  function roleField(id, config, existing = true) {
    const block = el('fieldset', undefined, 'role-setting'); block.dataset.role = id; block.append(el('legend', config.name || '새 역할'));
    for (const [key, label, type] of [['id','역할 ID','input'],['name','표시 이름','input'],['kind','역할 유형','select'],['model','모델 (선택)','input'],['instructions','역할 지침 (선택)','textarea'],['skills','스킬 경로 · 한 줄에 하나 (선택)','textarea']]) {
      const wrap = el('label', label); const input = el(type); input.name = `${id}_${key}`;
      if (key === 'kind') for (const [value, title] of Object.entries(kindNames)) input.append(new Option(title,value));
      input.value = key === 'id' ? id : key === 'skills' ? (config.skills || []).join('\n') : key === 'kind' ? config.kind || id : config[key];
      if (key === 'id') { input.required = true; input.pattern = '[a-z][a-z0-9_]{0,39}'; input.readOnly = existing; }
      if (key === 'name') input.required = true;
      if (key === 'kind' && id === 'plan') input.disabled = true;
      if (type === 'textarea') input.rows = 3;
      if (key === 'skills') input.placeholder = '.agents/skills/founder/SKILL.md';
      wrap.append(input); block.append(wrap);
    }
    const remove = el('button', '역할 제거', 'text-button'); remove.type = 'button'; remove.disabled = id === 'plan'; remove.addEventListener('click', () => block.remove()); block.append(remove); $('role-fields').append(block);
  }
  const stable = value => JSON.stringify(value, (_,v) => v && typeof v === 'object' && !Array.isArray(v) ? Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b))) : v);
  function settingsBody() {
    const form = $('settings-form'); const body = {version:settingsDraft.version,autopilot:form.elements.autopilot.checked,roles:{}};
    for (const key of ['max_rounds','max_minutes','max_tokens']) body[key] = Number(form.elements[key].value);
    for (const block of $('role-fields').children) {
      const key = block.dataset.role; const id = form.elements[`${key}_id`].value;
      if (Object.hasOwn(body.roles,id)) throw new Error('역할 ID가 중복됐습니다.');
      body.roles[id] = Object.fromEntries(['name','kind','model','instructions'].map(k => [k,form.elements[`${key}_${k}`].value]));
      body.roles[id].skills = form.elements[`${key}_skills`].value.split('\n').map(s=>s.trim()).filter(Boolean);
    }
    return body;
  }
  async function loadSettings() {
    const sequence = ++settingsSequence; const projectId = settingsProject; settingsLoadedProject = undefined; settingsDraft = undefined; $('save-settings').disabled = true;
    $('role-fields').replaceChildren();
    if (!crewsetCatalog) crewsetCatalog=await api('/api/team/catalog',undefined,projectId);
    const presets=$('crewset-role');presets.replaceChildren();
    for (const role of crewsetCatalog.roles) presets.append(new Option(role.label,role.id));
    $('crewset-source').textContent=`Crewset product-team · ${crewsetCatalog.source.commit.slice(0,8)} · 필요한 역할만 추가하세요. 기존 역할·모델·스킬은 유지합니다.`;
    const settings = await api('/api/team/settings', undefined, projectId);
    if (sequence !== settingsSequence || projectId !== settingsProject) return;
    settingsDraft = {...settings,roles:Object.fromEntries(Object.entries(settings.roles).map(([id,r])=>[id,{kind:r.kind || id,skills:r.skills || [],...r}]))};
    const form = $('settings-form'); form.elements.autopilot.checked = settings.autopilot;
    for (const key of ['max_rounds','max_minutes','max_tokens']) form.elements[key].value = settings[key];
    $('role-fields').replaceChildren();for (const [id,config] of Object.entries(settingsDraft.roles)) roleField(id,config);
    settingsLoadedProject = projectId; $('save-settings').disabled = false;
    $('settings-project').textContent = `${settingsProjects.find(p=>p.id===settingsProject)?.name || projectName()} · 프로젝트별 설정`;
  }
  $('add-crewset-role').addEventListener('click', () => {
    const role=crewsetCatalog?.roles.find(r=>r.id===$('crewset-role').value);if (!role) return;
    const exists=Array.from($('role-fields').children).some(block=>$('settings-form').elements[`${block.dataset.role}_id`].value===role.id);
    if (exists) { $('settings-error').textContent='이미 있는 역할입니다. 기존 설정을 유지했습니다.';$('settings-error').hidden=false;return; }
    roleField(role.id,role.config);$('settings-error').hidden=true;
  });
  $('add-role').addEventListener('click', () => { const id = `role_${Date.now()}_${++roleCounter}`; roleField(id,{name:'',kind:'assess',model:'',instructions:'',skills:[]},false); });
  $('settings-project-picker').addEventListener('change',async event=>{
    const previousProject=settingsProject; const requestedProject=event.target.value;
    try {
      if (settingsDraft && settingsLoadedProject === settingsProject && stable(settingsBody()) !== stable(settingsDraft)) { event.target.value = settingsProject; throw new Error('입력한 설정을 먼저 저장하거나 최신 설정을 불러온 뒤 프로젝트를 전환해주세요.'); }
      settingsProject=event.target.value; await loadSettings(); $('settings-error').hidden=true;
    } catch(e) { if (settingsProject !== requestedProject && settingsProject !== previousProject) return; settingsProject=previousProject;event.target.value=previousProject;$('settings-error').textContent=e.message; $('settings-error').hidden=false; }
  });
  $('open-settings').addEventListener('click', async () => {
    settingsProject=project();$('settings-error').hidden=true;
    try {
      const context = await api('/api/context',undefined,settingsProject);
      settingsProject=context.project?.id || settingsProject;
      settingsProjects = context.projects || [{id:settingsProject,name:projectName()}];
      const picker=$('settings-project-picker');picker.replaceChildren();
      for (const p of settingsProjects.filter(p=>p.orchestration_enabled!==false)) picker.append(new Option(p.name,p.id));
      picker.value=settingsProject;await loadSettings();open($('settings-dialog'));
    } catch(e) { showError(e.message); }
  });
  $('close-settings').addEventListener('click', () => close($('settings-dialog')));
  $('reload-settings').addEventListener('click', async () => { try { await loadSettings(); $('settings-error').hidden = true; } catch (e) { $('settings-error').textContent = e.message; $('settings-error').hidden = false; } });
  $('settings-form').addEventListener('submit', async event => {
    event.preventDefault(); if (!settingsDraft || settingsLoadedProject !== settingsProject) return; let body;
    try { body=settingsBody(); } catch(e) { $('settings-error').textContent=e.message;$('settings-error').hidden=false;return; }
    const projectId = settingsProject; const sequence = settingsSequence; $('save-settings').disabled = true;
    try { await api('/api/team/settings', body, projectId, 'PATCH'); if (sequence !== settingsSequence || projectId !== settingsProject) return; close($('settings-dialog')); notify('팀 설정을 저장했습니다. 다음 실행부터 적용됩니다.'); await loadRoom(); }
    catch (e) { $('settings-error').textContent = e.message; $('settings-error').hidden = false; } finally { if (sequence === settingsSequence) $('save-settings').disabled = settingsLoadedProject !== settingsProject; }
  });
  refresh(); const timer = setInterval(() => { if (!document.hidden) refresh(); }, 3000); window.addEventListener('pagehide', () => clearInterval(timer), { once: true });
})();
