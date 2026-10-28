"""Passive session linkage and metadata observation. Never starts agents."""
import hashlib
import json
import uuid
from pathlib import Path
from .store import BoardError, Store, timestamp
HOOKS = ('SessionStart', 'UserPromptSubmit', 'SubagentStart', 'SubagentStop', 'Stop', 'Interrupt', 'SessionEnd', 'PermissionRequest', 'PostCompact', 'PostToolUse')
TERMINAL = {'succeeded', 'failed', 'interrupted'}

def identifier(prefix):
    return prefix + uuid.uuid4().hex.upper()

def text(value, name, limit=30000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise BoardError(f'{name}: 비어 있지 않은 문자열이 필요합니다.')
    return value

class Tracker:
    def __init__(self,store,workspace):self.store,self.workspace=store,str(Path(workspace).resolve())
    def migrate(self):
        with self.store.connect() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='thread_bindings'").fetchone():return
        self.store.backup()
        with self.store.connect() as db:
            db.executescript("""BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS observations (id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL UNIQUE,thread_id TEXT NOT NULL,task_id TEXT,run_id TEXT,kind TEXT NOT NULL,data TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS thread_bindings (thread_id TEXT PRIMARY KEY,task_id TEXT NOT NULL);
                COMMIT;""")
    def overview(self):
        with self.store.connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='observations'").fetchone():return {'observations':[]}
            return {'observations':[dict(row) for row in db.execute('SELECT id,thread_id,task_id,run_id,kind,created_at FROM observations ORDER BY id DESC LIMIT 100')]}
    def bind(self,thread,task,expected_task=None):
        text(thread,'thread',200);self.store.detail(task);self.migrate()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute('SELECT task_id FROM thread_bindings WHERE thread_id=?',(thread,)).fetchone()
            if expected_task is not None and (not existing or existing[0]!=expected_task):
                raise BoardError('스레드의 현재 작업이 변경됐습니다. 연결을 다시 확인해주세요.',409)
            if existing and existing[0]!=task and expected_task is None:
                raise BoardError('이미 다른 작업에 연결됐습니다. 전환하려면 --from-task 기존작업ID를 명시하세요.',409)
            db.execute('INSERT INTO thread_bindings VALUES (?,?) ON CONFLICT(thread_id) DO UPDATE SET task_id=excluded.task_id',(thread,task))

    def observe(self,payload):
        if not isinstance(payload,dict): raise BoardError('이벤트 객체가 필요합니다.')
        kind=payload.get('hook_event_name');thread=payload.get('session_id')
        if kind not in HOOKS: raise BoardError('지원하지 않는 Hook입니다.')
        text(thread,'session_id',200)
        self.migrate()
        # Never persist prompt, tool input/output, transcript path, or raw hook payload.
        safe={k:payload[k] for k in ('source','agent_id','agent_type','reason','tool_name') if isinstance(payload.get(k),str) and len(payload[k])<=200}
        event_id=payload.get('event_id') or payload.get('tool_use_id')
        event_key=hashlib.sha256((thread+kind+str(event_id)).encode()).hexdigest() if event_id else identifier('EV-')
        with self.store.connect() as db:
            parent=payload.get('parent_session_id')
            row=db.execute('SELECT task_id FROM thread_bindings WHERE thread_id=?',(thread,)).fetchone()
            if not row and isinstance(parent,str):
                row=db.execute('SELECT task_id FROM thread_bindings WHERE thread_id=?',(parent,)).fetchone()
            task=row[0] if row else None
            if task:
                db.execute('INSERT OR IGNORE INTO thread_bindings VALUES (?,?)',(thread,task))
            run_id=None
            for run in (db.execute('SELECT id,data FROM executions ORDER BY created_at DESC') if db.execute("SELECT 1 FROM sqlite_master WHERE name='executions'").fetchone() else []):
                if json.loads(run['data']).get('thread_id')==thread:
                    run_id=run['id'];break
            db.execute('INSERT OR IGNORE INTO observations(event_key,thread_id,task_id,run_id,kind,data,created_at) VALUES (?,?,?,?,?,?,?)',(event_key,thread,task,run_id,kind,json.dumps(safe),timestamp()))
            if kind=='SubagentStart' and task and safe.get('agent_id'):
                db.execute('INSERT OR IGNORE INTO thread_bindings VALUES (?,?)',(safe['agent_id'],task))
        return {'recorded':True,'task_id':task,'thread_id':thread}


def hook_command(payload):
    from .projects import resolve
    try:
        project=resolve(payload.get('cwd',str(Path.cwd())))
    except BoardError as error:
        if error.status==404: return {}
        raise
    orch=Tracker(Store(project['db_path']),project['workspace'])
    result=orch.observe(payload)
    if payload.get('hook_event_name')=='SessionStart':
        context=f"Tack project {project['id']}. "
        context+=f"Linked task: {result['task_id']}. Read tack resume {result['task_id']} before continuing." if result['task_id'] else 'This session is not linked. For substantive development work select/create the correct task and run tack bind THREAD_ID TASK_ID. Do not attach to an arbitrary recent task.'
        return {'hookSpecificOutput':{'hookEventName':'SessionStart','additionalContext':context}}
    return {}
