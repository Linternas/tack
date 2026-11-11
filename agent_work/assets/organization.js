/* Team configuration and evidence-backed presentations. No model calls for a demo. */
window.TackOrganization = {
  mount(actions) {
    const el = (tag, text, cls) => {
        const e = document.createElement(tag);
        if (text != null) e.textContent = text;
        if (cls) e.className = cls;
        return e;
      },
      button = (text, fn, cls = 'button secondary') => {
        const b = el('button', text, cls);
        b.type = 'button';
        b.onclick = () => Promise.resolve().then(fn).catch(error);
        return b;
      };
    let data = null,
      session = '',
      index = 0,
      version = 0;
    const feedbacks = new Map();
    function error(e) {
      const n = document.getElementById('workspace-error');
      n.textContent = e.message;
      n.hidden = false;
    }
    function avatar(id) {
      const n = el('span', null, 'org-avatar'),
        look = window.TackOfficeScene?.look(id) ?? 0;
      n.style.backgroundImage = 'url(/office-avatar-' + look + '.png)';
      return n;
    }
    function field(form, label, name, value, type = 'input') {
      const row = el('label', label),
        input = el(type);
      input.name = name;
      input.value = value ?? '';
      row.append(input);
      form.append(row);
      return input;
    }
    function checks(form, label, name, values, selected = []) {
      const set = el('fieldset', null, 'org-checks');
      set.append(el('legend', label));
      for (const [id, text] of values) {
        const row = el('label'),
          input = el('input');
        input.type = 'checkbox';
        input.name = name;
        input.value = id;
        input.checked = selected.includes(id);
        row.append(input, el('span', text));
        set.append(row);
      }
      form.append(set);
      return set;
    }
    async function edit(team = null) {
      const [org, settings] = await Promise.all([actions.api('/api/organization'), actions.api('/api/team/settings')]);
      if (team) {
        team = org.teams[team.id];
        if (!team) throw Error('팀이 변경됐습니다. 최신 팀 목록을 확인해주세요.');
      }
      const dialog = el('dialog', null, 'workspace-dialog org-dialog'),
        form = el('form'),
        head = el('header', null, 'dialog-header');
      head.append(el('h2', team ? '팀 설정' : '새 팀 만들기'), button('×', () => dialog.close(), 'icon-button'));
      form.append(head);
      const body = el('div', null, 'form-body');
      form.append(body);
      const name = field(body, '팀 이름', 'name', team?.name || '');
      name.required = true;
      name.maxLength = 40;
      const kind = field(body, '조직 방식', 'kind', '', 'select');
      for (const [v, label] of [['product', '앱·제품 팀'], ['functional', '직군·지원 팀']]) {
        const o = el('option', label);
        o.value = v;
        kind.append(o);
      }
      kind.value = team?.kind || 'product';
      const paths = field(body, '담당 앱·패키지 경로 · 한 줄에 하나', 'paths', (team?.paths || []).join('\n'), 'textarea');
      paths.rows = 3;
      paths.placeholder = 'apps/chess\npackages/chess-ui';
      body.append(el('p', '지원만 하는 기획·QA팀은 경로를 비워둘 수 있습니다. 공유 패키지는 소유 팀 하나에 배정하세요.', 'dialog-guide'));
      const staffing = field(body, '역할 구성', 'staffing', '', 'select');
      for (const [v, label] of [['new', '기획·개발·QA 역할 3개 준비'], ['existing', '기존 역할 선택']]) {
        const o = el('option', label);
        o.value = v;
        staffing.append(o);
      }
      staffing.value = team ? 'existing' : 'new';
      if (team) staffing.disabled = true;
      const members = checks(body, '팀 구성원', 'member', Object.entries(settings.roles).map(([id, r]) => [id, r.name + ' · ' + ({
        plan: '기획',
        implement: '개발',
        verify: 'QA',
        assess: '평가'
      }[r.kind] || r.kind)]), team?.members);
      const sync = () => members.hidden = staffing.value !== 'existing';
      staffing.onchange = sync;
      kind.onchange = () => {
        if (!team && kind.value === 'functional') staffing.value = 'existing';
        sync();
      };
      sync();
      checks(body, '함께 일할 지원 팀', 'support', Object.entries(org.teams).filter(([id]) => id !== team?.id).map(([id, g]) => [id, g.name]), team?.support_teams);
      const parallel = field(body, '동시 실행 직원 수', 'max_parallel', team?.max_parallel || 2);
      parallel.type = 'number';
      parallel.min = 1;
      parallel.max = 8;
      parallel.required = true;
      const tokens = field(body, '팀 누적 토큰 예산', 'max_tokens', team?.max_tokens || 1000000);
      tokens.type = 'number';
      tokens.min = 1000;
      tokens.max = 20000000;
      tokens.required = true;
      const failure = el('p', null, 'form-error');
      failure.hidden = true;
      body.append(failure);
      const footer = el('footer', null, 'dialog-footer'),
        save = el('button', '팀 저장', 'button primary');
      footer.append(save);
      form.append(footer);
      dialog.append(form);
      document.body.append(dialog);
      dialog.addEventListener('close', () => dialog.remove());
      dialog.showModal();
      form.onsubmit = async e => {
        e.preventDefault();
        if (save.disabled) return;
        save.disabled = true;
        try {
          const payload = {
            version: org.version,
            name: name.value,
            kind: kind.value,
            paths: paths.value.split('\n').map(x => x.trim()).filter(Boolean),
            members: [...form.querySelectorAll('input[name=member]:checked')].map(n => n.value),
            support_teams: [...form.querySelectorAll('input[name=support]:checked')].map(n => n.value),
            max_parallel: Number(parallel.value),
            max_tokens: Number(tokens.value),
            staffing: staffing.value
          };
          await actions.api('/api/organization/teams' + (team ? '/' + team.id : ''), payload);
          dialog.close();
          await actions.refresh();
        } catch (e) {
          failure.textContent = e.message;
          failure.hidden = false;
        } finally {
          save.disabled = false;
        }
      };
    }
    function teams(root) {
      version++;
      const org = data.organization,
        toolbar = el('div', null, 'org-toolbar');
      toolbar.append(el('p', '팀의 역할을 미리 구성하세요. 사무실에서 팀과 역할을 선택하면 실제 Codex 세션을 시작할 수 있습니다.', 'office-guide'), button('+ 팀 만들기', () => edit(), 'button primary'));
      toolbar.lastChild.disabled = !data.execution_enabled;
      root.append(toolbar);
      const cards = el('div', null, 'org-teams');
      for (const group of Object.values(org.teams)) {
        const card = el('article', null, 'org-team-card'),
          top = el('div', null, 'org-team-heading');
        top.append(el('span', group.kind === 'product' ? 'PRODUCT TEAM' : 'FUNCTIONAL TEAM', 'section-kicker'), button('설정', () => edit(group), 'text-button'));
        top.lastChild.disabled = !data.execution_enabled;
        card.append(top, el('h2', group.name));
        const people = el('div', null, 'org-members');
        for (const id of group.members) {
          const role = data.settings.roles[id],
            person = el('div');
          person.append(avatar(id), el('strong', role?.name || id), el('small', {
            plan: '기획',
            implement: '개발',
            verify: 'QA',
            assess: '평가'
          }[role?.kind] || ''));
          people.append(person);
        }
        card.append(people);
        const paths = el('div', null, 'org-paths');
        for (const path of group.paths) paths.append(el('code', path));
        if (!group.paths.length) paths.append(el('span', '지원 업무 중심 · 직접 수정 경로 없음'));
        card.append(paths);
        if (group.support_teams.length) card.append(el('p', '지원: ' + group.support_teams.map(id => org.teams[id]?.name || id).join(', '), 'org-meta'));
        card.append(el('p', '동시 ' + group.max_parallel + '명 · ' + group.max_tokens.toLocaleString() + ' 토큰 예산', 'org-meta'));
        const links = el('div', null, 'org-team-actions');
        links.append(button('사무실 열기', () => actions.selectTeam(group.id)), button('목표 맡기기', () => actions.mission(group.id), 'button primary'));
        links.lastChild.disabled = !data.execution_enabled;
        card.append(links);
        cards.append(card);
      }
      root.append(cards);
      const assigned = new Set(Object.values(org.teams).flatMap(t => t.members)),
        free = Object.entries(data.settings.roles).filter(([id]) => !assigned.has(id));
      if (free.length) {
        const bank = el('section', null, 'org-unassigned');
        bank.append(el('h2', '미배치 역할'), el('p', '팀 설정에서 기존 역할을 선택해 배치할 수 있습니다.', 'office-guide'));
        const people = el('div', null, 'org-members');
        for (const [id, role] of free) {
          const item = el('div');
          item.append(avatar(id), el('strong', role.name));
          people.append(item);
        }
        bank.append(people);
        root.append(bank);
      }
    }
    async function publish() {
      const dialog = el('dialog', null, 'workspace-dialog org-dialog'),
        form = el('form'),
        header = el('header', null, 'dialog-header');
      header.append(el('h2', '데모데이 열기'), button('×', () => dialog.close(), 'icon-button'));
      form.append(header);
      const body = el('div', null, 'form-body'),
        title = field(body, '발표 제목', 'title', '팀 데모데이 · ' + new Date().toLocaleDateString('ko-KR'));
      title.required = true;
      title.maxLength = 120;
      const group = field(body, '발표할 팀', 'team', '', 'select'),
        all = el('option', '모든 팀');
      all.value = '';
      group.append(all);
      for (const [id, g] of Object.entries(data.organization.teams)) {
        const o = el('option', g.name);
        o.value = id;
        group.append(o);
      }
      body.append(el('p', '업무와 실제 검증을 마친 새 결과만 모읍니다. 발표 자체는 토큰을 사용하지 않습니다.', 'dialog-guide'));
      const failure = el('p', null, 'form-error');
      failure.hidden = true;
      body.append(failure);
      form.append(body);
      const footer = el('footer', null, 'dialog-footer'),
        save = el('button', '발표 시작', 'button primary');
      footer.append(save);
      form.append(footer);
      dialog.append(form);
      document.body.append(dialog);
      dialog.onclose = () => dialog.remove();
      dialog.showModal();
      form.onsubmit = async e => {
        e.preventDefault();
        if (save.disabled) return;
        save.disabled = true;
        try {
          const result = await actions.api('/api/demo-days', {
            title: title.value,
            team_id: group.value || null
          });
          session = result.id;
          index = 0;
          dialog.close();
          await actions.refresh();
          actions.redraw();
        } catch (e) {
          failure.textContent = e.message;
          failure.hidden = false;
        } finally {
          save.disabled = false;
        }
      };
    }
    function currentView() {
      return !document.body.dataset.officeView || document.body.dataset.officeView === 'demoday';
    }
    async function demos(root) {
      const sequence = ++version;
      root.replaceChildren(el('p', '발표 결과를 불러오는 중입니다.', 'office-guide'));
      try {
        const result = await actions.api('/api/demo-days');
        if (sequence !== version || !root.isConnected || !currentView()) return;
        root.replaceChildren();
        const toolbar = el('div', null, 'org-toolbar'),
          select = el('select', null, 'demo-picker');
        select.setAttribute('aria-label', '데모데이 선택');
        for (const d of result.demos) {
          const option = el('option', d.title);
          option.value = d.id;
          select.append(option);
        }
        if (!result.demos.some(d => d.id === session)) session = result.demos[0]?.id || '';
        select.value = session;
        select.onchange = () => {
          session = select.value;
          index = 0;
          demos(root);
        };
        toolbar.append(select, button('검증된 결과로 데모데이 열기', publish, 'button primary'));
        toolbar.lastChild.disabled = !data.execution_enabled;
        root.append(toolbar);
        const demo = result.demos.find(d => d.id === session);
        if (!demo) {
          const empty = el('div', null, 'demo-empty');
          empty.append(el('img'), el('h2', '다음 결과를 무대에서 만나세요'), el('p', '업무와 실제 검증을 마친 목표가 발표 대상입니다. 발표 자체는 모델을 호출하지 않습니다.'));
          empty.firstChild.src = '/office-demo-stage.png';
          empty.firstChild.alt = '빈 발표장';
          root.append(empty);
          return;
        }
        index = Math.min(index, demo.entries.length - 1);
        const entry = demo.entries[index],
          layout = el('div', null, 'demo-layout'),
          stage = el('section', null, 'demo-stage'),
          image = el('img');
        image.src = '/office-demo-stage.png';
        image.alt = '팀 데모데이 발표장';
        stage.append(image);
        const speaker = entry.reports.find(r => data.settings.roles[r.employee]?.kind === 'plan') || entry.reports[0],
          presenter = el('div', null, 'demo-presenter');
        if (speaker) {
          presenter.append(avatar(speaker.employee), el('strong', speaker.name));
        }
        stage.append(presenter);
        const label = el('div', null, 'demo-stage-caption');
        label.append(el('span', entry.team_name, 'section-kicker'), el('h2', entry.title), el('p', index + 1 + ' / ' + demo.entries.length + '번째 발표'));
        stage.append(label);
        layout.append(stage);
        const report = el('section', null, 'demo-report');
        report.append(el('span', entry.team_name + ' · ' + {
          pending: '확인 대기',
          accepted: '수락됨',
          changes_requested: '수정 요청됨',
          held: '보류'
        }[entry.decision], 'section-kicker'), el('h2', entry.title), el('p', entry.acceptance, 'demo-acceptance'));
        const facts = el('div', null, 'demo-facts');
        facts.append(el('span', '검증 ' + entry.verification.length + '건 통과'), el('span', entry.tokens.toLocaleString() + ' 토큰'), el('span', entry.reports.length + '개 업무 결과'));
        report.append(facts);
        const lead = entry.reports.find(r => data.settings.roles[r.employee]?.kind === 'implement') || entry.reports[0];
        if (lead) report.append(el('p', lead.body.slice(0, 900) + (lead.body.length > 900 ? '…' : ''), 'demo-overview'));
        for (const result of entry.reports) {
          const details = el('details', null, 'demo-result');
          details.append(el('summary', result.name + ' · ' + result.title), el('p', result.body));
          for (const file of result.files) details.append(el('code', file));
          report.append(details);
        }
        const checks = el('details', null, 'demo-result');
        checks.append(el('summary', '실제 검증 결과'));
        for (const check of entry.verification) checks.append(el('code', check.argv.join(' ') + ' · ' + check.status));
        report.append(checks);
        for (const url of entry.links) {
          const a = el('a', url, 'demo-link');
          a.href = url;
          a.target = '_blank';
          a.rel = 'noopener noreferrer';
          report.append(a);
        }
        if (entry.decision === 'pending' || entry.decision === 'held') {
          const feedback = el('textarea');
          feedback.value = feedbacks.get(demo.id + ':' + entry.goal_id) || '';
          feedback.oninput = () => feedbacks.set(demo.id + ':' + entry.goal_id, feedback.value);
          feedback.rows = 3;
          feedback.maxLength = 10000;
          feedback.placeholder = '수정할 내용이나 보류 이유를 적어주세요.';
          feedback.setAttribute('aria-label', '데모데이 피드백');
          report.append(feedback);
          const controls = el('div', null, 'demo-decisions');
          for (const [decision, label] of [['accept', '결과 수락'], ['revise', '수정 요청·팀에 전달'], ['hold', '보류']]) {
            const b = button(label, async () => {
              if (controls.dataset.saving === 'true') return;
              if (decision === 'revise' && !feedback.value.trim()) {
                feedback.reportValidity();
                feedback.focus();
                throw Error('수정할 내용을 적어주세요.');
              }
              controls.dataset.saving = 'true';
              for (const control of controls.children) control.disabled = true;
              try {
                const reply = await actions.api('/api/demo-days/' + demo.id + '/respond', {
                  version: demo.version,
                  goal_id: entry.goal_id,
                  decision,
                  feedback: feedback.value
                });
                if (reply.launch_error) error(Error('수정 목표는 등록됐습니다. ' + reply.launch_error));
                feedbacks.delete(demo.id + ':' + entry.goal_id);
                await actions.refresh();
                actions.redraw();
              } finally {
                delete controls.dataset.saving;
                for (const control of controls.children) control.disabled = !data.execution_enabled;
              }
            }, decision === 'accept' ? 'button primary' : 'button secondary');
            b.disabled = !data.execution_enabled;
            controls.append(b);
          }
          report.append(controls);
        } else if (entry.feedback) report.append(el('p', entry.feedback, 'demo-feedback'));
        if (entry.followup_goal) report.append(button('후속 목표 보기', () => actions.goal(entry.followup_goal)));
        layout.append(report);
        root.append(layout);
        const nav = el('div', null, 'demo-navigation'),
          prev = button('← 이전 팀', () => {
            index--;
            demos(root);
          }),
          next = button('다음 팀 →', () => {
            index++;
            demos(root);
          });
        prev.disabled = index === 0;
        next.disabled = index === demo.entries.length - 1;
        nav.append(prev, el('span', index + 1 + ' / ' + demo.entries.length), next);
        root.append(nav);
      } catch (e) {
        if (sequence === version && root.isConnected && currentView()) {
          root.replaceChildren(el('p', e.message, 'form-error'));
        }
      }
    }
    return {
      update(next) {
        data = next;
      },
      teams,
      demos,
      edit,
      publish
    };
  }
};
