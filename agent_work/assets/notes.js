(() => {
  const $ = id => document.getElementById(id);
  const editor = $('note-editor');
  const form = $('note-form');
  const rows = $('note-rows');
  const status = $('note-status');
  const more = $('more-notes');
  const error = $('note-error');
  const save = $('save-note');
  const title = form.elements.namedItem('title');
  const body = form.elements.namedItem('body');
  let current, query = '', offset = 0, listRequest = 0, opening = false, readonly = false;

  async function request(path, method = 'GET', payload) {
    const options = payload ? {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)} : {};
    const response = await fetch(window.workUrl(path), options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    return data;
  }

  async function load(append = false) {
    const requestId = ++listRequest;
    more.hidden = true;
    if (!append) {
      offset = 0;
      rows.replaceChildren();
    }
    status.textContent = '불러오는 중…';
    try {
      const params = new URLSearchParams({q: query, offset});
      const data = await request('/api/notes?' + params);
      if (requestId !== listRequest) return;
      for (const note of data.notes) {
        const row = rows.insertRow();
        const button = document.createElement('button');
        button.className = 'text-button';
        button.textContent = note.title;
        button.addEventListener('click', async () => {
          if (opening || editor.open) return;
          opening = true;
          try {
            open(await request('/api/notes/' + note.id));
          } catch (error) {
            status.textContent = error.message;
          } finally {
            opening = false;
          }
        });
        row.insertCell().append(button);
        row.insertCell().textContent = new Date(note.updated_at).toLocaleString('ko-KR');
      }
      offset += data.notes.length;
      more.hidden = !data.has_more;
      status.textContent = offset ? '' : (query ? '검색 결과가 없습니다.' : '아직 노트가 없습니다.');
    } catch (error) {
      if (requestId !== listRequest) return;
      status.textContent = error.message;
      more.hidden = !append;
    }
  }

  function open(note) {
    current = note;
    $('note-editor-title').textContent = readonly ? '노트 보기' : (note ? '노트 수정' : '노트 추가');
    title.value = note?.title ?? '';
    body.value = note?.body ?? '';
    title.readOnly = body.readOnly = readonly;
    save.hidden = readonly;
    error.hidden = true;
    editor.showModal();
  }

  function close() {
    if (save.disabled) return;
    if ((title.value !== (current?.title ?? '') || body.value !== (current?.body ?? ''))
        && !window.confirm('저장하지 않은 변경을 닫을까요?')) return;
    editor.close();
  }

  $('add-note').addEventListener('click', () => {
    if (!opening && !editor.open && !readonly) open(null);
  });
  $('close-note').addEventListener('click', close);
  editor.addEventListener('cancel', event => {
    event.preventDefault();
    close();
  });
  $('note-search').addEventListener('submit', event => {
    event.preventDefault();
    query = $('note-query').value.trim();
    load();
  });
  more.addEventListener('click', () => load(true));
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (save.disabled || readonly) return;
    save.disabled = true;
    error.hidden = true;
    const payload = {title: title.value.trim(), body: body.value};
    try {
      if (current) {
        await request('/api/notes/' + current.id, 'PATCH', {...payload, version: current.version});
      } else {
        await request('/api/notes', 'POST', payload);
      }
      editor.close();
      load();
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
    } finally {
      save.disabled = false;
    }
  });

  request('/api/context').then(data => {
    readonly = data.project.status === 'archived';
    $('add-note').disabled = readonly;
  }).catch(() => {});
  load();
})();
