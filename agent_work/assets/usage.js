(() => {
  const $ = id => document.getElementById(id);
  const phases = {initial:'최초 실행',revision:'수정 루프',retry:'재시도',question:'질문',legacy:'과거 실행 · 상세 미수집'};
  const count = value => value == null ? '미수집' : value.toLocaleString('ko-KR');
  const node = (tag,text,cls) => {const e=document.createElement(tag);if(text!=null)e.textContent=text;if(cls)e.className=cls;return e;};
  let sequence=0;
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
    if(!data.runs.length){const tr=node('tr'),td=node('td','수집된 실행이 없습니다. 새 하네스 실행부터 사용량이 기록됩니다.');td.colSpan=9;tr.append(td);list.append(tr);}
  }
  $('refresh-usage').addEventListener('click',()=>load(true));
  for(const id of ['usage-goal','usage-role'])$(id).addEventListener('change',()=>load());
  load(true);
})();
