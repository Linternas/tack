(() => {
  const $ = id => document.getElementById(id);
  const phases = {initial:'최초 실행',revision:'수정 루프',retry:'재시도',question:'질문',legacy:'과거 실행 · 상세 미수집'};
  const count = value => value == null ? '미수집' : value.toLocaleString('ko-KR');
  const node = (tag,text,cls) => {const e=document.createElement(tag);if(text!=null)e.textContent=text;if(cls)e.className=cls;return e;};
  const kinds = {goal:'목표', role:'역할 실행', session:'세션 실행'};
  const statuses = {
    awaiting_acceptance:'수락 대기', awaiting_approval:'승인 대기', blocked:'차단', cancelled:'취소',
    completed:'종료', done:'완료', failed:'실패', interrupted:'중단', needs_recheck:'재확인 필요',
    paused:'일시정지', queued:'실행 대기', ready:'준비', running:'실행 중', succeeded:'성공', unknown:'미기록',
    waiting_answer:'답변 대기',
  };
  const fieldNames = {
    acceptance:'완료 기준', adapter:'실행기', created_at:'생성 시각', error:'오류', exit_code:'종료 코드',
    goal_id:'목표 ID', id:'기록 ID', model:'모델 설정', role:'역할', status:'기록된 상태', summary:'결과 요약',
    task_id:'작업 ID', thread_id:'대화 ID', tokens:'기록된 토큰', updated_at:'마지막 기록',
    verification_results:'검증 결과', workspace:'작업 폴더',
  };
  const historyPageSize = 20;
  let sequence=0, detailSequence=0, historyLoading=false;

  const historyDate = value => {
    const date = new Date(value);
    return value && Number.isFinite(date.getTime()) ? date.toLocaleString('ko-KR') : value || '미기록';
  };
  const historyStatus = status => Object.hasOwn(statuses, status) ? statuses[status] : status || '미기록';

  async function read(path) {
    const response = await fetch(window.workUrl(path));
    const data = await response.json();
    if (!response.ok) throw Error(data.error || '기록을 불러오지 못했습니다.');
    return data;
  }

  function historyRow(item) {
    const row = node('tr');
    const title = node('td');
    const label = item.label || item.id;
    const detail = node('button', label, 'text-button');
    detail.type = 'button';
    detail.addEventListener('click', () => openExecution(item));
    title.append(detail, node('code', item.id, 'usage-sub'));
    if (item.workspace) title.append(node('span', item.workspace, 'usage-sub'));

    const task = node('td', item.task_id ? undefined : '—');
    if (item.task_id) {
      const link = node('a', item.task_id);
      link.href = window.workUrl('/?ticket=' + encodeURIComponent(item.task_id));
      task.append(link);
    }
    row.append(node('td', Object.hasOwn(kinds, item.kind) ? kinds[item.kind] : '과거 기록'), title,
      node('td', historyStatus(item.status)), node('td', historyDate(item.updated_at)), task);
    return row;
  }

  async function loadHistory(append=false) {
    if (historyLoading) return;
    historyLoading = true;
    const list = $('execution-rows');
    const status = $('execution-status');
    const more = $('more-executions');
    const offset = append ? list.children.length : 0;
    more.disabled = true;
    status.textContent = '실행 이력을 불러오는 중입니다.';
    try {
      const data = await read(`/api/executions?limit=${historyPageSize}&offset=${offset}`);
      if (!append) list.replaceChildren();
      for (const item of data.items) list.append(historyRow(item));
      $('execution-count').textContent = `${data.total}건`;
      more.hidden = !data.has_more;
      status.textContent = data.total ? '' : '보존된 실행 이력이 없습니다.';
    } catch (error) {
      status.textContent = '실행 이력 조회 실패: ' + error.message;
    } finally {
      more.disabled = false;
      historyLoading = false;
    }
  }

  async function openExecution(summary) {
    const seq = ++detailSequence;
    const dialog = $('execution-detail');
    const title = $('execution-title');
    const fields = $('execution-fields');
    const status = $('execution-detail-status');
    title.textContent = summary.label || summary.id;
    fields.replaceChildren();
    status.textContent = '실행 상세를 불러오는 중입니다.';
    if (!dialog.open) dialog.showModal();
    try {
      const {item, details} = await read('/api/executions/' + encodeURIComponent(summary.detail_key || summary.id));
      if (seq !== detailSequence || !dialog.open) return;
      const values = {...details, id: item.id, status: historyStatus(item.status), role: item.role, workspace: item.workspace,
        updated_at: historyDate(item.updated_at), task_id: item.task_id, goal_id: item.goal_id};
      for (const [key, value] of Object.entries(values)) {
        if (value == null || value === '') continue;
        const field = node('dd');
        if (typeof value === 'object') field.append(node('pre', JSON.stringify(value,null,2)));
        else field.textContent = value;
        fields.append(node('dt', Object.hasOwn(fieldNames, key) ? fieldNames[key] : key), field);
      }
      status.textContent = '';
    } catch (error) {
      if (seq === detailSequence && dialog.open) status.textContent = '실행 상세 조회 실패: ' + error.message;
    }
  }

  async function load(options=false) {
    const seq=++sequence;
    try {
      const url=new URL(window.workUrl('/api/usage'),location.origin);
      if(!options) {if($('usage-goal').value)url.searchParams.set('goal',$('usage-goal').value);if($('usage-role').value)url.searchParams.set('role',$('usage-role').value);}
      const response=await fetch(url.pathname+url.search);const data=await response.json();if(!response.ok)throw Error(data.error);if(seq!==sequence)return;
      if(options) {
        for(const [id,key] of [['usage-goal','goal_id'],['usage-role','role']]) {
          const select=$(id),selected=select.value;select.replaceChildren(node('option',key==='role'?'전체 역할':'전체 목표'));select.options[0].value='';
          for(const row of data.groups[key])if(row.name){const option=node('option',row.title||row.name);option.value=row.name;select.append(option);}
          select.value=selected;
        }
        if($('usage-goal').value||$('usage-role').value)return load();
      }
      $('usage-error').hidden=true;render(data);
    } catch(error) {if(seq===sequence){$('usage-error').textContent='사용량 조회 실패: '+error.message;$('usage-error').hidden=false;}}
  }
  function render(data) {
    const s=data.summary;const metrics=$('usage-metrics');metrics.replaceChildren();
    for(const [label,value,coverage] of [['수집된 총 토큰',count(s.total_tokens),`${s.total_tokens_measured_runs}/${s.runs}개 실행 총량 수집`],['입력 / 출력',`${count(s.input_tokens)} / ${count(s.output_tokens)}`,`입력 ${s.input_tokens_measured_runs} · 출력 ${s.output_tokens_measured_runs}개 실행 수집`],['캐시 재사용 비율',s.cache_ratio==null?'미수집':(s.cache_ratio*100).toFixed(1)+'%',`${s.cache_measured_runs}/${s.runs}개 실행에서 입력·캐시 모두 수집`],['실행 / 완료 턴',`${s.runs} / ${count(s.turns)}`,'완료 턴 수는 모델 호출 수와 다릅니다.']]) {
      const card=node('div',null,'usage-card');card.append(node('span',label),node('strong',value),node('small',coverage));metrics.append(card);
    }
    $('usage-note').textContent=data.note;
    for(const [id,key] of [['usage-roles','role'],['usage-phases','phase']]) {
      const root=$(id);root.replaceChildren();const rows=data.groups[key],max=Math.max(1,...rows.map(r=>r.total_tokens||0));
      for(const row of rows){const wrap=node('div',null,'usage-bar'),label=node('div',null,'usage-bar-label');label.append(node('span',key==='phase'?(phases[row.name]||row.name):(row.name||'역할 미수집')),node('span',count(row.total_tokens)+' · '+row.runs+'회'));wrap.append(label);if(row.total_tokens!=null){const bar=node('progress');bar.max=max;bar.value=row.total_tokens;bar.setAttribute('aria-label',(row.name||'미수집')+' 총 토큰 '+row.total_tokens);wrap.append(bar);}root.append(wrap);}
      if(!rows.length)root.append(node('p','수집된 실행이 없습니다.','muted'));
    }
    $('usage-count').textContent=`${s.runs}개 실행 · 최근 100개 표시`;
    const list=$('usage-runs');list.replaceChildren();
    for(const run of data.runs.slice(0,100)) {
      const tr=node('tr'),id=node('td');id.append(node('strong',run.id),node('span',`${run.goal_title||run.goal_id||'목표 없음'} / ${run.task_id}`,'usage-sub'),node('span',run.created_at,'usage-sub'));tr.append(id);
      for(const text of [`${run.role||'미수집'} · ${phases[run.phase]||run.phase}${!run.round_number?'':` · 수정 ${run.round_number}회차`}`,`${run.model||'기본 모델 · 미확인'} / ${run.adapter||'미수집'}`,count(run.input_tokens),count(run.cached_input_tokens),count(run.output_tokens),count(run.total_tokens),count(run.turns),`${run.duration_ms==null?'미수집':(run.duration_ms/1000).toFixed(1)+'초'} · ${run.status}`])tr.append(node('td',text));list.append(tr);
    }
    if(!data.runs.length){const tr=node('tr'),td=node('td','수집된 실행이 없습니다.');td.colSpan=9;tr.append(td);list.append(tr);}
  }
  $('refresh-usage').addEventListener('click',()=>{load(true);loadHistory();});
  $('more-executions').addEventListener('click',()=>loadHistory(true));
  for(const id of ['usage-goal','usage-role'])$(id).addEventListener('change',()=>load());
  load(true);
  loadHistory();
})();
