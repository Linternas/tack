(() => {
  const $=id=>document.getElementById(id);
  let selected; let action; let sequence=0; let pending=false;
  const titles={archive:'프로젝트 보관',restore:'프로젝트 보관 해제',delete:'Tack 기록 영구 삭제'};
  async function api(path,body) {
    const response=await fetch(path,body===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await response.json();if(!response.ok)throw new Error(data.error || '요청을 처리하지 못했습니다.');return data;
  }
  const close=()=>{ if($('project-dialog').close)$('project-dialog').close();else $('project-dialog').removeAttribute('open');selected=null;++sequence; };
  async function choose(project,kind) {
    const current=++sequence;selected=null;action=kind;
    $('project-dialog-title').textContent=`${titles[kind]} · ${project.name}`;
    $('project-action-scope').textContent='삭제 범위와 최신 상태를 확인하고 있습니다…';$('project-action-counts').textContent='';$('project-action-preserved').textContent='';$('project-action-error').textContent='';
    $('delete-options').hidden=true;$('project-confirmation').value='';$('include-backups').checked=false;$('confirm-project-action').disabled=true;
    if(!$('project-dialog').open){if($('project-dialog').showModal)$('project-dialog').showModal();else $('project-dialog').setAttribute('open','');}
    try {
      const detail=await api(`/api/projects/${encodeURIComponent(project.id)}`);if(current!==sequence)return;selected=detail;
      $('project-action-scope').textContent=kind==='archive'?'기록을 유지하고 기본 목록에서 숨깁니다. 보관 중에는 읽기 전용입니다. 외부 Codex·Orca 작업은 직접 종료한 뒤 진행하세요.':kind==='restore'?'같은 프로젝트 ID와 작업·세션 기록을 유지한 채 기본 목록으로 되돌립니다.':'이 프로젝트의 Tack 작업·세션·목표·실행·메시지·문서 기록을 영구 삭제합니다. 이 작업은 보관 해제로 복구할 수 없습니다.';
      const names={tasks:'작업',sessions:'세션',goals:'목표',executions:'실행',documents:'보관 문서',observations:'수신 이벤트'};
      $('project-action-counts').textContent=Object.entries(detail.counts).map(([key,value])=>`${names[key] || key}: ${value}`).join('\n');
      $('project-action-preserved').textContent=`유지: ${detail.preserved.join(', ')}`;
      $('delete-options').hidden=kind!=='delete';$('project-backups').textContent=detail.backup_paths.length?`기본은 백업 보존입니다. 함께 삭제 가능한 경로:\n${detail.backup_paths.join('\n')}`:'Tack 관리 백업·이관 자료가 없습니다.';
      const blocked=kind!=='restore'&&!detail.can_archive || kind==='delete'&&detail.status!=='archived';
      $('project-action-error').textContent=blocked?detail.blocked_reason || '먼저 프로젝트를 보관해주세요.':'';
      $('confirm-project-action').textContent=titles[kind];$('confirm-project-action').className='button '+(kind==='delete'?'danger':'primary');$('confirm-project-action').disabled=!!blocked;
    } catch(error){if(current===sequence)$('project-action-error').textContent=error.message;}
  }
  async function refresh() {
    try {
      const data=await api('/api/projects'+($('show-archived').checked?'?all=1':''));const list=$('project-list');list.replaceChildren();
      for(const project of data.projects) {
        const card=document.createElement('article');card.dataset.project=project.id;
        const link=document.createElement('a');link.href=`/?project=${encodeURIComponent(project.id)}`;link.textContent=project.name+(project.status==='archived'?' · 보관':'');
        const path=document.createElement('p');path.textContent=project.workspace;
        const counts=document.createElement('p');counts.textContent=project.error || `미완료 ${project.open_tasks} · 완료 ${project.done_tasks} · 재개 세션 ${project.active_sessions}`;card.append(link,path,counts);
        const button=document.createElement('button');button.type='button';button.className='button secondary';button.textContent=project.status==='archived'?'보관 해제':'보관';button.addEventListener('click',()=>choose(project,project.status==='archived'?'restore':'archive'));card.append(button);
        if(project.status==='archived') {const remove=document.createElement('button');remove.type='button';remove.className='button danger';remove.textContent='Tack 기록 영구 삭제';remove.addEventListener('click',()=>choose(project,'delete'));card.append(remove);}
        list.append(card);
      }
      if(!data.projects.length)list.textContent='표시할 프로젝트가 없습니다. 보관 프로젝트 포함 여부를 확인하세요.';
      $('project-error').textContent='';
    } catch(error){$('project-error').textContent=error.message;}
  }
  $('show-archived').addEventListener('change',refresh);$('refresh-projects').addEventListener('click',refresh);
  $('cancel-project-action').addEventListener('click',()=>{if(!pending)close();});
  $('project-dialog').addEventListener('cancel',event=>{if(pending)event.preventDefault();else{selected=null;++sequence;}});
  $('project-action-form').addEventListener('submit',async event=>{
    event.preventDefault();if(!selected || pending || $('confirm-project-action').disabled)return;
    if(action==='delete'&&$('project-confirmation').value!==selected.name){$('project-action-error').textContent='프로젝트 이름을 정확하게 입력해주세요.';return;}
    const project=selected;const kind=action;const payload={version:project.version};
    if(kind==='delete')Object.assign(payload,{confirmation:$('project-confirmation').value,include_backups:$('include-backups').checked});
    pending=true;$('confirm-project-action').disabled=true;$('cancel-project-action').disabled=true;
    try {const result=await api(`/api/projects/${encodeURIComponent(project.id)}/${kind}`,payload);close();await refresh();$('project-error').textContent=result.warning || (kind==='delete'&&result.remaining_backup_paths.length?'Tack 기록을 삭제했습니다. 선택하지 않은 관리 백업은 보존했습니다.':`${titles[kind]} 완료`);}
    catch(error){$('project-action-error').textContent=error.message+' 최신 상태를 확인하려면 취소 후 다시 열어주세요.';}
    finally{pending=false;$('cancel-project-action').disabled=false;}
  });
  refresh();
})();
