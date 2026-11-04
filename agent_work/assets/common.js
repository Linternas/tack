(() => {
  const selected = new URLSearchParams(location.search).get('project');
  window.workUrl = (path) => {
    const url = new URL(path, location.origin);
    if (selected) url.searchParams.set('project', selected);
    return url.pathname + url.search;
  };
  const sidebar=document.querySelector('.sidebar');
  if(sidebar && !sidebar.querySelector('a[href="/usage"]')) {const link=document.createElement('a');link.href='/usage';link.className='nav-item';link.textContent='사용량';sidebar.append(link);}
  for (const link of document.querySelectorAll('a[href^="/"]')) link.href = window.workUrl(link.getAttribute('href'));
  const picker = document.getElementById('project-picker');
  fetch(window.workUrl('/api/context')).then(async (response) => {
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    if (data.orchestration_enabled) for (const link of document.querySelectorAll('.sidebar a[href]')) {if(new URL(link.href,location.origin).pathname==='/goals'){link.href=window.workUrl('/team');link.textContent='팀 워크스페이스';}}
    if (!data.orchestration_enabled) for (const link of document.querySelectorAll('a[href]')) { if (['/goals','/team'].includes(new URL(link.href, location.origin).pathname)) link.remove(); }
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
