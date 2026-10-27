(() => {
  const selected = new URLSearchParams(location.search).get('project');
  window.workUrl = (path) => {
    const url = new URL(path, location.origin);
    if (selected) url.searchParams.set('project', selected);
    return url.pathname + url.search;
  };
  for (const link of document.querySelectorAll('a[href^="/"]')) link.href = window.workUrl(link.getAttribute('href'));
  const picker = document.getElementById('project-picker');
  fetch(window.workUrl('/api/context')).then(async (response) => {
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    document.title = `${data.project.name} · Tack`;
    const currentProject = document.getElementById('current-project');
    if (currentProject) currentProject.textContent = data.project.name;
    for (const project of data.projects) {
      const option = document.createElement('option');
      option.value = project.id;
      option.textContent = project.name;
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
