"""Actual Codex threads as employees. Team membership is optional Tack metadata."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import threading
import time
from .codex_app_server import AppServer
from .organization import Organization
from .execution_policy import within
from .store import BoardError, timestamp
from .team import Team, skill_sources


def profiles():
    homes = [Path(os.environ.get('CODEX_HOME', Path.home()/'.codex')), Path.home()/'.codex', Path.home()/'.codex-company']
    result = {}
    for home in homes:
        home = home.expanduser().resolve()
        if home.is_dir():
            key = hashlib.sha256(str(home).encode()).hexdigest()[:12]
            result[key] = {'id': key, 'name': home.name, 'home': str(home)}
    return result


def connect(workspace, profile):
    server = AppServer(workspace, codex_home=profile['home'])
    try:
        server.call('initialize', {'clientInfo': {'name': 'tack-office', 'version': '0.2.0'}, 'capabilities': {'experimentalApi': True}}, lambda _: None, lambda: None)
        server.write({'method': 'initialized', 'params': {}})
        return server
    except BaseException:
        server.close()
        raise


def observed(thread, profile):
    """Only bounded lifecycle metadata is read; conversation text is never copied."""
    path = thread.get('path')
    if not path:
        return None
    path = Path(path)
    home = Path(profile['home']).resolve()
    if not any(path.resolve().is_relative_to(home/part) for part in ('sessions', 'archived_sessions')):
        return None
    try:
        with path.open('rb') as stream:
            stream.seek(0, 2)
            start = max(0, stream.tell()-262144)
            stream.seek(start)
            lines = stream.read(262144).splitlines()
        last = None
        for line in (lines[1:] if start else lines):
            try:
                value = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            kind = value.get('payload', {}).get('type') if value.get('type') == 'event_msg' else None
            if kind in {'task_started', 'task_complete', 'turn_aborted'}:
                last = {'kind': kind, 'at': value.get('timestamp')}
        return last
    except OSError:
        return None


class SessionOffice:
    def __init__(self, store, workspace):
        self.store, self.workspace = store, str(Path(workspace).resolve())
        self.lock = threading.RLock()
        self.cache = {}
        self.running = {}
        self.closed = False

    def assignments(self):
        with self.store.connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='office_sessions'").fetchone():
                return {}
            return {row['id']: dict(row) for row in db.execute('SELECT * FROM office_sessions WHERE workspace=?', (self.workspace,))}

    def save(self, key, payload, check=True):
        if not isinstance(payload, dict) or set(payload) != {'version', 'team_id', 'role_id'} or type(payload['version']) is not int:
            raise BoardError('최신 연결 버전과 팀·역할을 확인해주세요.')
        if check:
            self.find(key)
        team_id, role_id = payload['team_id'], payload['role_id']
        if team_id is not None:
            group = Organization(self.store, self.workspace).scope(team_id)
            if role_id is not None and role_id not in group['members']:
                raise BoardError('선택한 팀에 속한 역할을 선택해주세요.')
        elif role_id is not None:
            raise BoardError('역할을 연결하려면 팀을 선택해주세요.')
        with self.lock:
            with self.store.connect() as db:
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='office_sessions'").fetchone()
            if not exists:
                self.store.backup()
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('CREATE TABLE IF NOT EXISTS office_sessions (workspace TEXT NOT NULL,id TEXT NOT NULL,version INTEGER NOT NULL,team_id TEXT,role_id TEXT,updated_at TEXT NOT NULL,PRIMARY KEY(workspace,id))')
                row = db.execute('SELECT version FROM office_sessions WHERE workspace=? AND id=?', (self.workspace, key)).fetchone()
                if payload['version'] != (row['version'] if row else 0):
                    raise BoardError('세션의 팀 연결이 변경됐습니다. 새로고침 후 다시 확인해주세요.', 409)
                db.execute('INSERT INTO office_sessions VALUES (?,?,?,?,?,?) ON CONFLICT(workspace,id) DO UPDATE SET version=excluded.version,team_id=excluded.team_id,role_id=excluded.role_id,updated_at=excluded.updated_at', (self.workspace, key, payload['version']+1, team_id, role_id, timestamp()))
        return {'id': key, 'version': payload['version']+1, 'team_id': team_id, 'role_id': role_id}

    def page(self, profile, cursor=None):
        cache_key = (profile['id'], cursor)
        with self.lock:
            cached = self.cache.get(cache_key)
            if cached and time.monotonic()-cached[0] < 10:
                return cached[1]
        server = connect(self.workspace, profile)
        try:
            params = {'limit': 100, 'sortKey': 'updated_at', 'modelProviders': [], 'sourceKinds': ['cli', 'vscode', 'exec', 'appServer', 'subAgent', 'subAgentReview', 'subAgentCompact', 'subAgentThreadSpawn', 'subAgentOther', 'unknown'], 'useStateDbOnly': True}
            if cursor:
                params['cursor'] = cursor
            result = server.call('thread/list', params, lambda _: None, lambda: None)
            rows = []
            for thread in result.get('data', []):
                thread_id = thread.get('id', '')
                if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', thread_id):
                    continue
                rows.append({'id': profile['id']+':'+thread_id, 'thread_id': thread_id, 'profile': profile['id'], 'profile_name': profile['name'], 'name': (thread.get('name') or thread.get('agentNickname') or thread.get('preview') or 'Codex 세션')[:120], 'cwd': thread.get('cwd', ''), 'model': thread.get('model'), 'source': thread.get('source'), 'updated_at': thread.get('updatedAt'), 'parent_thread_id': thread.get('parentThreadId') or ((thread.get('source') or {}).get('subAgent', {}).get('thread_spawn', {}).get('parent_thread_id') if isinstance(thread.get('source'),dict) else None), 'is_subagent': isinstance(thread.get('source'),dict) and 'subAgent' in thread['source'], '_path': thread.get('path')})
            result = {'sessions': rows, 'next_cursor': result.get('nextCursor')}
            with self.lock:
                self.cache[cache_key] = (time.monotonic(), result)
                if len(self.cache) > 40:
                    self.cache = {cache_key: self.cache[cache_key]}
            return result
        finally:
            server.close()

    def row(self, raw, assignments):
        row = {k: v for k, v in raw.items() if k != '_path'}
        row.update({k: v for k, v in assignments.get(row['id'], {}).items() if k in {'version', 'team_id', 'role_id'}})
        row.setdefault('version', 0)
        row.setdefault('team_id', None)
        row.setdefault('role_id', None)
        with self.lock:
            owned = self.running.get(row['id'])
            runtime = dict(owned['public']) if owned else None
        row.update(runtime or {'state': 'external', 'activity': '외부 Codex 세션 · 실시간 상태 미연결', 'controlled': False})
        if not owned:
            event = observed({'path': raw.get('_path')}, profiles()[row['profile']])
            row['last_event'] = event
            if event:
                row['activity'] = {'task_started': '마지막 기록: 작업 시작', 'task_complete': '마지막 기록: 응답 종료', 'turn_aborted': '마지막 기록: 중단'}[event['kind']]
        row['resume_command'] = 'CODEX_HOME='+shlex.quote(profiles()[row['profile']]['home'])+' codex resume '+shlex.quote(row['thread_id'])
        return row

    def list(self, profile_id=None, cursor=None, view='current', scope='project'):
        if view not in {'current','history'} or scope not in {'project','all'}:
            raise BoardError('현재 작업 또는 세션 기록과 조회 범위를 선택해주세요.')
        if view=='current' and (cursor or profile_id):
            raise BoardError('과거 세션 페이지는 세션 기록에서 조회해주세요.')
        if cursor is not None and (not profile_id or not isinstance(cursor,str) or len(cursor)>4096):
            raise BoardError('프로필과 페이지 커서를 확인해주세요.')
        available = profiles()
        if profile_id and profile_id not in available:
            raise BoardError('등록된 Codex 프로필을 선택해주세요.')
        assignments = self.assignments()
        rows, pages, errors = {}, {}, []
        for key, profile in available.items():
            if profile_id and key != profile_id:
                continue
            if view=='current':continue
            try:
                page = self.page(profile, cursor)
                rows.update({r['id']: r for r in page['sessions']})
                pages[key] = page['next_cursor']
            except (BoardError, OSError) as error:
                errors.append({'profile': profile['name'], 'message': str(error)[:300]})
        with self.lock:
            for key, runtime in self.running.items():
                if not profile_id or runtime['raw']['profile'] == profile_id:
                    rows[key] = runtime['raw']
        sessions=[self.row(r, assignments) for r in rows.values()]
        def in_project(row):
            return bool(row.get('cwd')) and Path(row['cwd']).resolve().is_relative_to(self.workspace)
        if view=='current':
            sessions=[r for r in sessions if in_project(r) and r.get('controlled') and r['state'] in {'working','awaiting_approval','waiting_user'}]
        elif scope=='project':
            sessions=[r for r in sessions if in_project(r)]
        return {'view':view,'scope':'project' if view=='current' else scope,'sessions':sessions, 'next_cursors': pages, 'profiles': [{'id': p['id'], 'name': p['name']} for p in available.values()], 'errors': errors}

    def find(self, key):
        if not isinstance(key, str) or not re.fullmatch(r'[a-f0-9]{12}:[a-zA-Z0-9_-]{1,100}', key):
            raise BoardError('유효한 Codex 세션을 선택해주세요.')
        with self.lock:
            owned = self.running.get(key)
            cached = [r for _, page in self.cache.values() for r in page['sessions'] if r['id'] == key]
        if owned:
            return owned['raw']
        if cached:
            return cached[0]
        profile_id, thread_id = key.split(':', 1)
        profile = profiles().get(profile_id)
        if not profile:
            raise BoardError('등록된 Codex 프로필을 선택해주세요.')
        server = connect(self.workspace, profile)
        try:
            thread = server.call('thread/read', {'threadId': thread_id, 'includeTurns': False}, lambda _: None, lambda: None).get('thread', {})
            if thread.get('id') != thread_id:
                raise BoardError('Codex 세션을 찾을 수 없습니다.', 404)
            return {'id': key, 'thread_id': thread_id, 'profile': profile_id, 'profile_name': profile['name'], 'cwd': thread.get('cwd', ''), 'name': (thread.get('name') or thread.get('preview') or 'Codex 세션')[:120], 'model': thread.get('model'), 'updated_at': thread.get('updatedAt'), '_path': thread.get('path')}
        finally:
            server.close()

    def start(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'profile', 'team_id', 'role_id', 'prompt'}:
            raise BoardError('프로필·선택 팀·역할·첫 지시를 확인해주세요.')
        prompt = payload['prompt']
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 20000:
            raise BoardError('첫 지시를 20,000자 이내로 입력해주세요.')
        profile = profiles().get(payload['profile'])
        if not profile:
            raise BoardError('등록된 Codex 프로필을 선택해주세요.')
        team_id, role_id = payload['team_id'], payload['role_id']
        settings = Team(self.store, self.workspace).settings()
        role = None
        cwd = self.workspace
        write_paths = settings['permissions']['write_paths']
        if team_id is not None:
            group = Organization(self.store, self.workspace).scope(team_id)
            if role_id not in group['members']:
                raise BoardError('팀에서 시작할 역할을 선택해주세요.')
            role = settings['roles'].get(role_id)
            if not role:
                raise BoardError('팀의 역할 설정이 변경됐습니다. 다시 확인해주세요.',409)
            write_paths = list(dict.fromkeys(a if within(a,b) else b for a in write_paths for b in group['paths'] if within(a,b) or within(b,a)))
        write = role is None or role.get('kind') == 'implement'
        write_roots = [str((Path(self.workspace)/p).resolve()) for p in write_paths]
        if write:
            if not write_roots or any(not Path(p).is_dir() or not Path(p).is_relative_to(self.workspace) for p in write_roots):
                raise BoardError('프로젝트·팀의 수정 허용 폴더가 프로젝트 안에 실제로 존재해야 합니다.',403)
            cwd = write_roots[0]
        if team_id is None and role_id is not None:
            raise BoardError('역할을 사용하려면 팀을 선택해주세요.')
        # A direct session has no planner gate. The user's prompt is its initial scope.
        with self.lock:
            if self.closed:
                raise BoardError('사무실이 종료 중입니다.', 409)
            if sum(r['public']['state'] in {'working', 'awaiting_approval'} for r in self.running.values()) >= 3:
                raise BoardError('최대 3개 세션을 동시에 실행할 수 있습니다. 실행 종료 후 다시 시작해주세요.', 409)
            loaded=[r for r in self.running.values() if r['public']['controlled']]
            if len(loaded)>=12:
                idle=next((r for r in loaded if r['public']['state'] not in {'working','awaiting_approval'}),None)
                if idle:
                    idle['server'].close()
                    idle['public'].update(controlled=False,state='external',activity='저장된 세션 · Codex에서 재개 가능')
            server = connect(cwd, profile)
            try:
                params = {'cwd': cwd, 'approvalPolicy': 'untrusted', 'sandbox': 'workspace-write' if write else 'read-only', 'ephemeral': False}
                params['developerInstructions'] = 'Work only on the user request in this project. Project protected paths: '+json.dumps(settings['permissions']['protected_paths'])+'. Do not modify protected paths or perform remote Git writes, deployment, or other external side effects without explicit user authorization.'
                if role:
                    params['developerInstructions'] += '\n'+role['instructions']+'\n'+ '\n'.join('Skill source: '+path+'\n'+content for path,content in skill_sources(self.workspace,role))
                    if role.get('model'):
                        params['model'] = role['model']
                thread = server.call('thread/start', params, lambda _: None, lambda: None)['thread']
                thread_id = thread['id']
                key = profile['id']+':'+thread_id
                raw = {'id': key, 'thread_id': thread_id, 'profile': profile['id'], 'profile_name': profile['name'], 'name': role['name'] if role else prompt.strip()[:80], 'cwd': cwd, 'model': thread.get('model'), 'source': 'appServer', 'updated_at': time.time()}
                self.save(key, {'version': 0, 'team_id': team_id, 'role_id': role_id}, check=False)
                self.running[key] = {'server': server, 'raw': raw, 'cancel': threading.Event(), 'write_roots': write_roots if write else None, 'public': {'state': 'idle', 'activity': '시작 준비', 'controlled': True, 'last_message': '', 'error': '', 'tokens': None}}
            except BaseException:
                server.close()
                raise
            def request(value):
                if value['method'] not in {'item/commandExecution/requestApproval', 'item/fileChange/requestApproval'}:
                    return False
                with self.lock:
                    runtime = self.running[key]
                    runtime.setdefault('pending', []).append(value)
                    self._show_approval(runtime)
                return True
            server.request_handler = request
            try:
                self.send(key, {'prompt': prompt})
            except BaseException:
                server.close()
                self.running.pop(key, None)
                raise
        return self.row(raw, self.assignments())

    def execution(self, key, status, server):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if status == 'running':
                db.execute('CREATE TABLE IF NOT EXISTS office_executions (id TEXT PRIMARY KEY,data TEXT NOT NULL,updated_at TEXT NOT NULL)')
                row = db.execute('SELECT data FROM office_executions WHERE id=?', (key,)).fetchone()
                from .project_lifecycle import office_execution_active
                if row and office_execution_active(json.loads(row['data'])):
                    raise BoardError('기존 세션 실행의 종료를 먼저 확인해주세요.', 409)
                process = getattr(server, 'proc', None)
                data = {'status': status, 'process_group': process.pid if process else None}
                db.execute('INSERT INTO office_executions VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at', (key, json.dumps(data), timestamp()))
            else:
                db.execute("UPDATE office_executions SET data=json_set(data,'$.status',?),updated_at=? WHERE id=?", (status, timestamp(), key))

    def send(self, key, payload):
        if not isinstance(payload, dict) or set(payload) != {'prompt'} or not isinstance(payload['prompt'], str) or not 1 <= len(payload['prompt'].strip()) <= 20000:
            raise BoardError('지시를 20,000자 이내로 입력해주세요.')
        with self.lock:
            runtime = self.running.get(key)
            if not runtime or self.closed or not runtime['public']['controlled']:
                raise BoardError('외부 세션은 Codex에서 이어가세요. Tack에서 시작한 세션만 여기서 지시할 수 있습니다.', 409)
            if runtime['public']['state'] in {'working', 'awaiting_approval'}:
                raise BoardError('실행 중입니다. 응답이 끝나거나 중단한 후 지시해주세요.', 409)
            if sum(r['public']['state'] in {'working', 'awaiting_approval'} for r in self.running.values()) >= 3:
                raise BoardError('동시 실행 한도는 3개입니다.', 409)
            self.execution(key, 'running', runtime['server'])
            runtime['cancel'].clear()
            runtime['public'].update(state='working', activity='Codex 응답 대기', error='', last_message='')
        def run():
            server = runtime['server']
            turn_id = None
            def event(value):
                if value.get('method') == 'turn/completed':
                    turn = value.get('params', {}).get('turn', {})
                    runtime['completed_turn'] = turn.get('id')
                    runtime['turn_status'] = turn.get('status')
                    if turn.get('error'):
                        runtime['public']['error'] = str(turn['error'].get('message', 'Codex 실행 실패'))[:500]
                params = value.get('params', {})
                if params.get('threadId') and params['threadId'] != runtime['raw']['thread_id']:
                    return
                with self.lock:
                    public = runtime['public']
                    if value['method'] == 'item/completed' and params.get('item', {}).get('type') == 'agentMessage':
                        public['last_message'] = params['item'].get('text', '')[-8000:]
                    if value['method'] == 'thread/tokenUsage/updated':
                        public['tokens'] = params.get('tokenUsage', {}).get('total', {}).get('totalTokens')
                    public['activity'] = {'item/started': '도구 실행 중', 'item/agentMessage/delta': '답변 작성 중'}.get(value['method'], public['activity'])
            try:
                result = server.call('turn/start', {'threadId': runtime['raw']['thread_id'], 'input': [{'type': 'text', 'text': payload['prompt']}], 'cwd': runtime['raw']['cwd'], 'approvalPolicy': 'untrusted', 'sandboxPolicy': {'type': 'workspaceWrite', 'writableRoots': runtime['write_roots'], 'networkAccess': False, 'excludeTmpdirEnvVar': True, 'excludeSlashTmp': True} if runtime['write_roots'] else {'type': 'readOnly'}}, event, lambda: None)
                turn_id = result['turn']['id']
                deadline = time.monotonic()+1800
                interrupted = False
                def cancel():
                    nonlocal interrupted
                    if runtime['cancel'].is_set() and not interrupted:
                        self._decline(runtime)
                        server.send('turn/interrupt', {'threadId': runtime['raw']['thread_id'], 'turnId': turn_id})
                        interrupted = True
                    return None
                while runtime.get('completed_turn') != turn_id:
                    value = server.receive(event, cancel, deadline)
                    if value.get('notification'):
                        # receive dispatches notifications; completion is captured below by handler.
                        if runtime.get('completed_turn') == turn_id:
                            break
                    if time.monotonic() > deadline:
                        raise BoardError('세션 실행 시간 한도(30분)에 도달했습니다.', 504)
                with self.lock:
                    public = runtime['public']
                    final = runtime.get('turn_status')
                    public['state'] = 'paused' if interrupted or final == 'interrupted' else 'idle' if final == 'completed' else 'failed'
                    public.pop('approval', None)
                    public['activity'] = {'paused': '중단됨', 'failed': '실행 실패', 'idle': '응답 종료 · 다음 지시 대기'}[public['state']]
            except (BoardError, OSError, KeyError) as error:
                server.close()
                with self.lock:
                    runtime['public'].update(state='failed', activity='연결 종료', error=str(error)[:500], controlled=False)
            finally:
                with self.lock:
                    if runtime['public']['state'] in {'working', 'awaiting_approval'}:
                        server.close()
                        runtime['public'].update(state='failed', activity='연결 종료', controlled=False)
                    self.execution(key, runtime['public']['state'], server)
                    self.cache.clear()
        worker = threading.Thread(target=run, daemon=True)
        runtime['worker'] = worker
        try:
            worker.start()
        except BaseException:
            runtime.pop('worker', None)
            runtime['server'].close()
            runtime['public'].update(state='failed', controlled=False)
            self.execution(key, 'failed', runtime['server'])
            raise
        return {'id': key, 'state': 'working'}

    @staticmethod
    def _show_approval(runtime):
        value=runtime['pending'][0]
        params=value.get('params', {})
        runtime['public'].update(state='awaiting_approval',activity='실행 승인 필요',approval={'id':value['id'],'method':value['method'],'command':str(params.get('command','파일 변경'))[:1000],'reason':str(params.get('reason',''))[:500]})

    def _decline(self, runtime):
        with self.lock:
            requests = runtime.pop('pending', [])
            runtime['public'].pop('approval', None)
        for request in requests:
            runtime['server'].write({'id': request['id'], 'result': {'decision': 'decline'}})

    def approve(self, key, payload):
        if not isinstance(payload, dict) or set(payload) != {'request_id', 'decision'} or payload['decision'] not in {'accept', 'decline'}:
            raise BoardError('요청 ID와 승인 여부를 확인해주세요.')
        with self.lock:
            runtime = self.running.get(key)
            if not runtime or not runtime.get('pending') or runtime['pending'][0]['id'] != payload['request_id']:
                raise BoardError('실행 승인 요청이 바뀌었습니다.', 409)
            request=runtime['pending'].pop(0)
            runtime['server'].write({'id': request['id'], 'result': {'decision': payload['decision']}})
            if runtime['pending']:
                self._show_approval(runtime)
            else:
                runtime['public'].pop('approval', None)
                runtime['public'].update(state='working', activity='Codex 실행 중')
        return {'id': key, 'decision': payload['decision']}

    def interrupt(self, key):
        with self.lock:
            runtime = self.running.get(key)
            if not runtime or not runtime['public']['controlled']:
                raise BoardError('Tack에서 시작한 세션만 중단할 수 있습니다.', 409)
            runtime['cancel'].set()
        return {'id': key, 'interrupt_requested': True}

    def close(self):
        with self.lock:
            self.closed = True
            runtimes = list(self.running.values())
        for runtime in runtimes:
            runtime['cancel'].set()
            runtime['server'].close()
        for runtime in runtimes:
            if runtime.get('worker'):
                runtime['worker'].join(timeout=2)
