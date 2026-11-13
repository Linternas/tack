"""Connect actual local Herdr agent executions to Tack tasks."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
from .projects import resolve
from .records import Records, bounded
from .store import BoardError, Store
from .tracking import Tracker


def context(address):
    if not re.fullmatch(r't[0-9a-z]+', address):
        raise BoardError('이 PC의 Herdr 에이전트를 선택해주세요.')
    try:
        result = subprocess.run(['hc', 'get', address, '--json'], capture_output=True, text=True, timeout=5)
    except subprocess.TimeoutExpired:
        raise BoardError('Herdr 실행 정보를 가져오는 시간이 초과됐습니다.', 504)
    if result.returncode:
        raise BoardError('Herdr에서 해당 실행을 찾을 수 없습니다.', 404)
    terminal = json.loads(result.stdout)
    if terminal.get('address') != address or type(terminal.get('created_ms')) is not int:
        raise BoardError('Herdr 실행 식별자를 확인할 수 없습니다.')
    labels = terminal.get('labels') or {}
    if labels.get('tack_workspace'):
        project = resolve(labels['tack_workspace'])
        store = Store(project['db_path'], readonly=True)
        with store.connect() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='executions'").fetchone()
            rows = db.execute('SELECT * FROM executions WHERE goal_id=? AND task_id=?', (labels.get('tack_goal'), labels.get('tack_job'))).fetchall() if exists else []
        execution = next((row for row in reversed(rows) if json.loads(row['data']).get('herdr_address') == address), None)
        foreground = terminal.get('foreground') or {}
        state = terminal.get('status') or {}
        owns_process = state.get('title') == json.loads(execution['data']).get('herdr_title') if execution and state.get('title') else False
        if execution and (terminal['state'] != 'live' or foreground.get('name') in {'sh','bash','zsh','fish','nu'} or owns_process or json.loads(execution['data']).get('status') == 'running'):
            return {'key':execution['id'], 'address':address, 'program':labels.get('tack_role'),
                    'state':terminal['state'], 'cwd':project['workspace'], 'task_id':execution['task_id'],
                    'goal_id':execution['goal_id'], 'managed':True}
    process = terminal.get('foreground') or {}
    status = terminal.get('status') or {}
    if terminal.get('state') != 'live' or not status.get('app') or type(process.get('pid')) is not int or process.get('name') in {'sh','bash','zsh','fish','nu'}:
        raise BoardError('실행 중인 에이전트 직원을 선택해주세요.')
    cwd = process.get('cwd') or terminal.get('cwd')
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise BoardError('에이전트의 실제 작업 폴더를 확인할 수 없습니다.')
    # A terminal can host a new agent later. Bind the process incarnation as well.
    identity = json.dumps([address, terminal.get('created_ms'), process['pid'], status['app'], cwd])
    key = 'herdr-' + hashlib.sha256(identity.encode()).hexdigest()
    return {'key': key, 'address': address, 'program': status['app'], 'state': status.get('state'), 'cwd': cwd}


def snapshot(session, query=''):
    if len(query)>200:
        raise BoardError('검색어는 200자 이내로 입력해주세요.')
    project = resolve(session['cwd'])
    store = Store(project['db_path'], readonly=True)
    with store.connect() as db:
        row = db.execute('SELECT task_id FROM thread_bindings WHERE thread_id=?', (session['key'],)).fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE name='thread_bindings'").fetchone() else None
    records = Records(store)
    tasks = [t for t in records.heads() if t['status'] not in {'done', 'archived'}]
    term = query.strip().casefold()
    tasks = [t for t in tasks if not term or term in (t['id']+' '+t['title']).casefold()]
    tasks.sort(key=lambda t: (t['status'] == 'in_progress', t['updated_at']), reverse=True)
    task = None
    task_id = session.get('task_id') or (row['task_id'] if row else None)
    if task_id:
        task = records.overview(task_id)['task']
        detail = store.detail(task_id)['task']
        task['verification'] = bounded(detail['verification'], 800)
        task['resolution'] = bounded(detail['resolution'], 800)
        task['qa_result'] = detail['qa_result']
        task['qa_actual'] = bounded(detail['qa']['actual'], 800)
        evidence = records.list(task['id'], kind='verification', limit=1)['records']
        if evidence:
            task['verification'] = bounded(records.show(evidence[0]['id'])['text'], 800)
        task['dependencies'] = [{'id': key, 'queue': store.detail(key)['task']['queue'], 'status': store.detail(key)['task']['status']} for key in task['blocked_by']]
    return {'session': session, 'project': {k: project[k] for k in ('id', 'name', 'status', 'workspace')}, 'task': task,
            'tasks': [{k: t[k] for k in ('id', 'title', 'queue', 'status')} for t in tasks[:12]], 'total_tasks': len(tasks)}


def dispatch(args):
    if args.herdr_action == 'team':
        return team_dispatch(args)
    session = context(args.address)
    if args.herdr_action == 'inspect':
        return snapshot(session, args.query)
    if session.get('managed'):
        raise BoardError('오케스트레이션 업무 연결은 컨트롤러가 관리합니다.',409)
    if session['key'] != args.session:
        raise BoardError('에이전트 실행이 바뀌었습니다. 새로고침 후 다시 연결해주세요.', 409)
    project = resolve(session['cwd'])
    if project['status'] != 'active':
        raise BoardError('현재 프로젝트는 기록할 수 없습니다.', 409)
    store = Store(project['db_path'])
    if args.herdr_action == 'bind':
        Tracker(store, session['cwd']).bind(session['key'], args.task_id, args.from_task)
    else:
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='thread_bindings'").fetchone():
                raise BoardError('연결된 작업이 없습니다.', 409)
            row = db.execute('SELECT task_id FROM thread_bindings WHERE thread_id=?', (session['key'],)).fetchone()
            if not row or row['task_id'] != args.from_task:
                raise BoardError('작업 연결이 변경됐습니다. 새로고침 후 확인해주세요.', 409)
            db.execute('DELETE FROM thread_bindings WHERE thread_id=?', (session['key'],))
    return snapshot(context(args.address))


def team_dispatch(args):
    """Core forwards only explicitly installed project orchestration commands."""
    import os
    project = resolve(args.folder)
    root = Path(project['workspace'])
    if project['status'] != 'active':
        raise BoardError('보관 프로젝트는 실행할 수 없습니다.',409)
    marker = root/'.tack-orchestration/install.json'
    if not marker.is_file():
        if args.action == 'status':
            return {'enabled':False,'workspace':str(root),'project':project['name']}
        raise BoardError('프로젝트에 tack-orchestration setup을 먼저 실행해주세요.',403)
    installed = json.loads(marker.read_text())
    if installed.get('format') != 1 or installed.get('workspace') != str(root) or installed.get('project_id') != project['id']:
        raise BoardError('프로젝트 하네스 설치 정보를 확인해주세요.',409)
    if os.environ.get('TACK_HARNESS') == '1' and Path(__file__).with_name('herdr_team.py').is_file():
        from .herdr_team import dispatch
        return dispatch(args,project)
    binary = root/'.tack-orchestration/bin/tack-orchestration'
    if not binary.resolve().is_relative_to(root) or not binary.is_file():
        raise BoardError('프로젝트 로컬 하네스 실행기를 확인해주세요.')
    command = [str(binary),'herdr','team','--folder',str(root),'--action',args.action,'--payload',args.payload]
    if args.goal:
        command += ['--goal',args.goal]
    if args.parent:
        command += ['--parent',args.parent]
    result = subprocess.run(command,capture_output=True,text=True,timeout=20)
    if result.returncode:
        try:
            error=json.loads(result.stderr)
        except ValueError:
            raise BoardError('프로젝트 하네스를 업데이트해주세요. '+result.stderr[:300])
        raise BoardError(error.get('error','하네스 요청 실패'),error.get('status') or 400)
    return json.loads(result.stdout)
