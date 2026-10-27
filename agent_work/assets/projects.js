(async () => {
  try {
    const response = await fetch(window.workUrl('/api/projects'));
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    const list = document.getElementById('project-list');
    for (const project of data.projects) {
      const card = document.createElement('article');
      const link = document.createElement('a');
      link.href = `/?project=${encodeURIComponent(project.id)}`;
      link.textContent = project.name;
      const path = document.createElement('p');
      path.textContent = project.workspace;
      const counts = document.createElement('p');
      counts.textContent = project.error || `미완료 ${project.open_tasks} · 완료 ${project.done_tasks} · 재개 세션 ${project.active_sessions}`;
      card.append(link, path, counts);
      list.append(card);
    }
    if (!data.projects.length) list.textContent = '등록된 프로젝트가 없습니다.';
  } catch (error) { document.getElementById('project-error').textContent = error.message; }
})();
