"""Native Office controls reuse the same workforce and queue as the dashboard."""
import json
from pathlib import Path
import re
import subprocess
from .records import bounded
from .store import BoardError, Store
from .team import Team
from .workforce import Workforce
from .orchestration_cli import start
from .run_queue import cancel


def dispatch(args, project):
    store = Store(project['db_path'])
    work = Workforce(store, project['workspace'])
    data = json.loads(args.payload)
    if not isinstance(data, dict):
        raise BoardError('요청은 JSON 객체여야 합니다.')
    key = args.goal
    action = args.action
    parent = getattr(args,'parent',None)
    if parent:
        if not re.fullmatch(r't[0-9a-z]+',parent):
            raise BoardError('이 PC의 Herdr 원본 터미널이 필요합니다.')
        response = subprocess.run(['hc','get',parent,'--json'],capture_output=True,text=True,timeout=5,check=True)
        terminal = json.loads(response.stdout)
        cwd = (terminal.get('foreground') or {}).get('cwd') or terminal.get('cwd')
        if terminal.get('state') != 'live' or not cwd or not Path(cwd).resolve().is_relative_to(Path(project['workspace']).resolve()):
            raise BoardError('현재 프로젝트에서 실행 중인 원본 터미널을 선택해주세요.',409)
    if action == 'connect':
        if any(g.get('worker') for g in work.orch.overview()['goals']):
            raise BoardError('현재 실행 종료 후 Herdr 실행 연결을 변경해주세요.', 409)
        work.team.save({**work.team.settings(), 'runtime':'herdr-codex-exec'})
    elif action == 'create':
        if set(data) != {'title', 'acceptance', 'verification_text'}:
            raise BoardError('목표·완료 조건·검증 명령을 입력해주세요.')
        if work.team.settings()['runtime'] != 'herdr-codex-exec':
            raise BoardError('Herdr 실행 연결을 먼저 선택해주세요.')
        goal = work.create(**data)
        key = goal['id']
        if parent:
            work.orch.update(key,{'herdr_parent':parent})
            goal = work.goal(key)
        start(project, key, goal['version'])
    elif action not in {'status', 'connect'}:
        if not key:
            raise BoardError('운영할 목표를 선택해주세요.')
        if parent and action in {'run','approve'}:
            goal = work.goal(key)
            if not goal.get('worker') and goal.get('herdr_parent') != parent:
                if set(data) != {'version'}:
                    raise BoardError('최신 목표 버전이 필요합니다.')
                work.orch.update(key,{'herdr_parent':parent},data['version'])
                data = {'version':work.goal(key)['version']}
        if action == 'assign':
            work.assign(key, data)
        elif action in {'approve', 'accept', 'run', 'pause'}:
            if set(data) != {'version'}:
                raise BoardError('최신 목표 버전이 필요합니다.')
            if action == 'pause':
                cancel(work, key, data['version'])
            elif action == 'run':
                start(project, key, data['version'])
            else:
                getattr(work, action)(key, data['version'])
        elif action in {'reply', 'retry'}:
            payload = dict(data)
            job = payload.pop('job_id', None)
            getattr(work, action)(key, job, payload)
        if action in {'approve', 'reply', 'retry'}:
            goal = work.goal(key)
            if not goal.get('worker'):
                start(project, key, goal['version'])
    goals = [g for g in work.orch.overview()['goals'] if g.get('mode') == 'team']
    goals.sort(key=lambda g:g['updated_at'], reverse=True)
    selected = work.snapshot(key) if key else None
    runs = selected['runs'] if selected else []
    jobs = []
    for job in selected['jobs'] if selected else []:
        jobs.append({**{k:job.get(k) for k in ('id','title','employee','status','kind','version','depends_on','files','wait_reason','question_decision')},
                     'instructions':bounded(job['instructions'],1200), 'result':bounded(job['result'], 1800), 'question':bounded(job['question'], 2000),
                     'terminal':next((r.get('herdr_address') for r in reversed(runs) if r.get('job_id') == job['id'] and r.get('herdr_address')), None)})
    settings = work.team.settings()
    return {'enabled':True, 'workspace':project['workspace'], 'project':project['name'],
            'runtime':settings['runtime'], 'mode':settings['execution_mode'],
            'roles':[{'id':k, 'name':v['name'], 'kind':v['kind']} for k,v in settings['roles'].items()],
            'goals':[{k:g.get(k) for k in ('id','title','status','version')} for g in goals[:20]],
            'total_goals':len(goals),
            'goal':{k:selected['goal'].get(k) for k in ('id','title','status','version','error','worker','token_used')} if selected else None,
            'acceptance':bounded(selected['goal']['acceptance'],1600) if selected else '',
            'verification_results':[{k:check.get(k) for k in ('argv','status','exit_code')} for check in selected['goal'].get('verification_results',[])] if selected else [],
            'scheduler':selected['scheduler'] if selected else None, 'queue':selected['queue'] if selected else None,
            'jobs':jobs, 'messages':[{**{k:m[k] for k in ('id','sender','kind')},'body':bounded(m['body'],1000)} for m in selected['messages'][-10:]] if selected else []}
