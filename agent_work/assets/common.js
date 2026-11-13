(() => {
  const selected = new URLSearchParams(location.search).get('project');
  window.workUrl = (path) => {
    const url = new URL(path, location.origin);
    if (selected) url.searchParams.set('project', selected);
    return url.pathname + url.search;
  };
  // Navigation belongs to the app shell; project capabilities only affect availability.
  let sidebar=document.querySelector('.sidebar');
  if(!sidebar){sidebar=document.createElement('aside');document.body.prepend(sidebar);}
  let pickerInSidebar=sidebar.querySelector('.project-picker');
  const legacyPicker=sidebar.querySelector('#project-picker');
  if(legacyPicker && !pickerInSidebar){pickerInSidebar=document.createElement('label');pickerInSidebar.textContent='프로젝트';pickerInSidebar.append(legacyPicker);}
  const settings=sidebar.querySelector('#open-settings');
  if(settings){settings.className='button secondary';settings.textContent='팀 설정';document.querySelector('main .heading-actions')?.prepend(settings);}
  if(pickerInSidebar){pickerInSidebar.classList.add('shell-project-picker');document.querySelector('main')?.prepend(pickerInSidebar);}
  sidebar.className='sidebar app-navigation';sidebar.setAttribute('aria-label','앱 메뉴');
  const menu=[['/team','팀 워크스페이스'],['/','작업 보드'],['/sessions','작업 재개·세션'],['/qa','QA 워크플로우'],['/projects','전체 프로젝트'],['/documents','보관 문서'],['/usage','사용량']];
  const current=location.pathname==='/goals'?'/team':location.pathname.startsWith('/documents/')?'/documents':location.pathname;
  sidebar.innerHTML='<a class="brand" href="/" aria-label="Tack 홈" title="Tack"><span class="brand-symbol">T</span></a><nav aria-label="주 메뉴">'+menu.map(([href,label])=>`<a class="nav-item${current===href?' active':''}" href="${href}" title="${label}" aria-label="${label}"${current===href?' aria-current="page"':''}><span class="nav-icon" aria-hidden="true"></span><span class="nav-label">${label}</span></a>`).join('')+'</nav>';
  // Older pages had their own navigation row in place of the shared sidebar.
  const oldNav=document.querySelector('main.session-layout > nav');
  if(oldNav){for(const link of oldNav.querySelectorAll('a'))link.remove();if(!oldNav.children.length)oldNav.remove();}
  const iconPaths={
    grid:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    issue:'<circle cx="12" cy="12" r="9"/><path d="M12 7v6m0 4h.01"/>',
    decision:'<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="7" r="2"/><path d="M6 7v10m2-4h3a7 7 0 0 0 7-7"/>',
    api:'<path d="M3 7h18m-4-4 4 4-4 4M21 17H3m4-4-4 4 4 4"/>',
    qa:'<rect x="5" y="5" width="14" height="16" rx="2"/><rect x="9" y="3" width="6" height="4" rx="1"/><path d="m9 14 2 2 4-4"/>',
    done:'<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    archived:'<rect x="3" y="3" width="18" height="5" rx="1.5"/><path d="M5 8v11a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8m-10 4h6"/>',
    team:'<circle cx="9" cy="8" r="3"/><path d="M3 21v-2a6 6 0 0 1 12 0v2m2-16a3 3 0 0 1 0 6m2 10v-2a6 6 0 0 0-3-5"/>',
    sessions:'<path d="M3 10a9 9 0 1 1 2 8M3 4v6h6m3-4v6l4 2"/>',
    projects:'<path d="M3 7V5a2 2 0 0 1 2-2h5l3 3h6a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/><path d="M8 11v5m4-5v3m4-3v7"/>',
    documents:'<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Zm0 0v6h6M8 13h8m-8 4h6"/>',
    usage:'<path d="M4 3v17a1 1 0 0 0 1 1h16M9 16v-5m5 5V6m5 10V9"/>',
    settings:'<path d="M4 7h5m4 0h7M4 17h9m4 0h3"/><circle cx="11" cy="7" r="2"/><circle cx="15" cy="17" r="2"/>'
  };
  window.tackIcon=name=>iconPaths[name]?`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${iconPaths[name]}</svg>`:'';
  const navIcons={'/':'grid','/goals':'team','/team':'team','/sessions':'sessions','/projects':'projects','/documents':'documents','/usage':'usage','/qa':'qa'};
  for(const link of document.querySelectorAll('.sidebar .nav-item')){
    const icon=link.matches('a[href]')?navIcons[new URL(link.getAttribute('href'),location.origin).pathname]:link.id==='open-settings'?'settings':null;
    if(!icon)continue;
    let mark=link.querySelector(':scope > .nav-icon');
    if(!mark){
      const label=document.createElement('span');label.className='nav-label';label.textContent=link.textContent.replace(/^[◎⚙]\s*/, '');
      mark=document.createElement('span');mark.className='nav-icon';mark.setAttribute('aria-hidden','true');link.replaceChildren(mark,label);
    }
    mark.innerHTML=window.tackIcon(icon);
  }
  for (const link of document.querySelectorAll('a[href^="/"]')) link.href = window.workUrl(link.getAttribute('href'));
  const picker = document.getElementById('project-picker');
  fetch(window.workUrl('/api/context')).then(async (response) => {
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    const teamLink=sidebar.querySelector('a[href^="/team"]');
    if(teamLink && !data.orchestration_enabled){
      teamLink.setAttribute('aria-disabled','true');
      teamLink.title='팀 워크스페이스 · 프로젝트에 오케스트레이션 설치 필요';
      teamLink.setAttribute('aria-label',teamLink.title);
      teamLink.addEventListener('click',event=>event.preventDefault());
    }
    document.title = `${data.project.name} · Tack`;
    const currentProject = document.getElementById('current-project');
    if (currentProject) currentProject.textContent = data.project.name;
    for (const project of data.projects) {
      const option = document.createElement('option');
      option.value = project.id;
      option.textContent = project.name + (project.status === 'archived' ? ' (보관)' : '');
      option.selected = project.id === data.project.id;
      picker?.append(option);
    }
    if (picker) picker.addEventListener('change', () => {
      const url = new URL(location.href);
      url.searchParams.set('project', picker.value);
      location.assign(url);
    });
  }).catch((error) => {
    if (picker) {
      const option = document.createElement('option');
      option.textContent = `프로젝트 로드 실패: ${error.message}`;
      picker.append(option);
    }
  });
})();
