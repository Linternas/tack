(() => {
  const $ = (id) => document.getElementById(id);
  const fields = { goal: '목표', decisions: '결정', changed_files: '변경 파일', verification: '검증 결과', blockers: '막힌 이유', next_action: '다음 행동' };
  let current;
  let initial = '';
  let saving = false;
  let sequence = 0;
  const signature = () => JSON.stringify([...new FormData($('checkpoint-form')).entries()]);
  const dirty = () => current && initial !== signature();
  const canLeave = () => !saving && (!dirty() || window.confirm('저장하지 않은 체크포인트가 있습니다. 이동할까요?'));
  async function request(path, options = {}) {
    const response = await fetch(window.workUrl(path), { ...options, headers: { 'Content-Type': 'application/json' } });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    return data;
  }
  async function load() {
    const data = await request(`/api/sessions${$('include-completed').checked ? '?all=1' : ''}`);
    $('session-list').replaceChildren();
    for (const session of data.sessions) {
      const card = document.createElement('article');
      const button = document.createElement('button');
      button.textContent = `${session.task_id} · ${session.goal} · ${session.status}`;
      button.addEventListener('click', () => { if (canLeave()) open(session.id).catch(fail); });
      card.append(button);
      const meta = document.createElement('p');
      meta.textContent = `${session.branch || '브랜치 미기록'} · ${session.updated_at}`;
      card.append(meta);
      $('session-list').append(card);
    }
    if (!data.sessions.length) $('session-list').textContent = '재개할 세션이 없습니다. CLI의 tack checkpoint로 작업에 세션을 연결할 수 있습니다.';
  }
  function fail(error) { $('session-error').textContent = error.message; }
  async function open(id) {
    const currentSequence = ++sequence;
    const data = await request(`/api/sessions/${id}`);
    if (currentSequence !== sequence) return;
    current = data.session;
    $('session-detail').hidden = false;
    $('checkpoint-fields').replaceChildren();
    for (const [name, title] of Object.entries(fields)) {
      const label = document.createElement('label');
      label.textContent = title;
      const input = document.createElement('textarea');
      input.name = name;
      input.maxLength = 30000;
      input.value = current[name];
      label.append(input);
      $('checkpoint-fields').append(label);
    }
    $('checkpoint-form').elements.status.value = current.status;
    initial = signature();
    $('checkpoint-history').replaceChildren();
    for (const checkpoint of data.checkpoints) {
      const details = document.createElement('details');
      const title = document.createElement('summary');
      title.textContent = checkpoint.created_at;
      const pre = document.createElement('pre');
      pre.textContent = JSON.stringify(JSON.parse(checkpoint.data), null, 2);
      details.append(title, pre);
      $('checkpoint-history').append(details);
    }
  }
  $('checkpoint-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (saving || !current) return;
    const body = { ...Object.fromEntries(new FormData(event.target)), version: current.version };
    const sessionId = current.id;
    saving = true;
    sequence++;
    const buttons = [...event.target.querySelectorAll('button')];
    buttons.forEach((button) => { button.disabled = true; });
    try {
      await request(`/api/sessions/${sessionId}`,  { method: 'PATCH', body: JSON.stringify(body) });
      await open(sessionId);
      await load();
      $('session-error').textContent = '저장 완료';
    } catch (error) { fail(error); }
    finally { saving = false; buttons.forEach((button) => { button.disabled = false; }); }
  });
  $('reload-session').addEventListener('click', () => { if (current && canLeave()) open(current.id).catch(fail); });
  $('include-completed').addEventListener('change', () => load().catch(fail));
  window.addEventListener('beforeunload', (event) => { if (dirty() || saving) { event.preventDefault(); event.returnValue = ''; } });
  load().catch(fail);
})();
