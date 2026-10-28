"""Project team policy and append-only task conversations. No model calls for rendering."""
import copy
from contextlib import nullcontext
from pathlib import Path
import json
import re
import time
from .store import BoardError, timestamp
from .orchestration import identifier, text

ROLES=('plan','implement','verify')
KINDS={'plan','implement','verify','assess'}
DEFAULTS={'autopilot':False,'max_rounds':2,'max_minutes':30,'max_tokens':500000,
          'roles':{'plan':{'name':'기획','model':'','instructions':''},'implement':{'name':'구현','model':'','instructions':'승인된 파일과 완료 조건 안에서 구현한다. 실제 변경과 수행한 검증, 미실행 검증을 구분해 전달한다.'},'verify':{'name':'검토','model':'','instructions':'완료 조건과 실제 코드·검증 근거를 대조한다. 결함은 영향과 재현·수정 조건으로 보고하고, 수행하지 않은 검증을 통과로 표시하지 않는다.'}}}

class Team:
    def __init__(self,store,workspace=None):self.store,self.workspace=store,workspace
    def exists(self):
        with self.store.connect() as db:return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='team_messages'").fetchone())
    def migrate(self):
        if self.exists():return
        self.store.backup()
        with self.store.connect() as db:
            db.executescript('''BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS team_settings (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, data TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS team_messages (id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, event_key TEXT NOT NULL UNIQUE, sender TEXT NOT NULL, target TEXT, kind TEXT NOT NULL, body TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS team_messages_goal ON team_messages(goal_id,created_at);
                COMMIT;''')
    @staticmethod
    def roles(roles):
        if any(not isinstance(value,dict) for value in roles.values()):raise BoardError('역할 설정은 객체여야 합니다.')
        return {key:{'kind':key if key in ROLES else 'assess','skills':[],**value} for key,value in roles.items()}
    def settings(self):
        if self.exists():
            with self.store.connect() as db:row=db.execute('SELECT * FROM team_settings WHERE id=1').fetchone()
            if row:
                data=json.loads(row['data']);data['roles']=self.roles(data['roles']);return {**data,'version':row['version']}
        return {**copy.deepcopy(DEFAULTS),'roles':self.roles(copy.deepcopy(DEFAULTS['roles'])),'version':0}
    def save(self,payload,connection=None):
        if not isinstance(payload,dict) or set(payload)!=set(DEFAULTS)|{'version'}:raise BoardError('팀 설정 필드를 확인해주세요.')
        if type(payload['version']) is not int or type(payload['autopilot']) is not bool:raise BoardError('버전과 실행 모드를 확인해주세요.')
        for name,low,high in [('max_rounds',0,20),('max_minutes',1,120),('max_tokens',1000,2000000)]:
            if type(payload[name]) is not int or not low<=payload[name]<=high:raise BoardError(f'{name}: {low}~{high} 범위가 필요합니다.')
        roles=payload['roles']
        if not isinstance(roles,dict) or not 3<=len(roles)<=12 or 'plan' not in roles:raise BoardError('plan 역할을 포함해 3~12개 역할이 필요합니다.')
        if any(not isinstance(role,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}',role) or role in {'user','tack'} for role in roles):raise BoardError('역할 ID는 영문 소문자로 시작하는 snake_case여야 합니다.')
        roles=self.roles(roles)
        names=[]
        for role,config in roles.items():
            if not isinstance(config,dict) or set(config)!={'name','model','instructions','kind','skills'}:raise BoardError('역할 설정 형식을 확인해주세요.')
            name=text(config['name'],'역할 이름',24)
            if name!=name.strip() or re.search(r'\s|@',name) or name.lower() in {'user','tack'} or (name in roles and name!=role):raise BoardError('역할 이름에는 공백/@를 사용할 수 없습니다.')
            names.append(name)
            if not isinstance(config['kind'],str) or config['kind'] not in KINDS or role=='plan' and config['kind']!='plan':raise BoardError('역할 유형을 확인해주세요.')
            if not isinstance(config['skills'],list) or len(config['skills'])>10 or any(not isinstance(v,str) or not v.strip() or len(v)>2000 for v in config['skills']):raise BoardError('스킬은 SKILL.md 경로 목록이어야 합니다.')
            if not isinstance(config['model'],str) or len(config['model'])>100 or (config['model'] and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*',config['model'])):raise BoardError('모델 이름을 확인해주세요.')
            if not isinstance(config['instructions'],str) or len(config['instructions'])>6000:raise BoardError('역할 지침은 6000자 이하입니다.')
        if len(set(names))!=len(roles):raise BoardError('역할 이름은 서로 달라야 합니다.')
        if not {'plan','implement','verify'}<={r['kind'] for r in roles.values()}:raise BoardError('계획·구현·검증 유형이 각각 하나 이상 필요합니다.')
        if self.workspace:
            for config in roles.values():skill_sources(self.workspace,config)
        self.migrate();data={**{k:payload[k] for k in DEFAULTS},'roles':roles}
        with nullcontext(connection) if connection is not None else self.store.connect() as db:
            if connection is None:db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT version FROM team_settings WHERE id=1').fetchone()
            if payload['version']!=(row[0] if row else 0):raise BoardError('다른 곳에서 설정을 변경했습니다. 입력을 보존하고 최신 설정을 확인해주세요.',409)
            db.execute('INSERT INTO team_settings VALUES (1,?,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version,data=excluded.data,updated_at=excluded.updated_at',(payload['version']+1,json.dumps(data,ensure_ascii=False),timestamp()))
        return {**data,'version':payload['version']+1}
    @staticmethod
    def insert(db,goal,sender,body,kind='note',target=None,data=None,key=None):
        message_id=identifier('MSG-');now=timestamp();key=key or message_id
        db.execute('INSERT OR IGNORE INTO team_messages VALUES (?,?,?,?,?,?,?,?,?)',(message_id,goal,key,sender,target,kind,body,json.dumps(data or {},ensure_ascii=False),now))
        row=db.execute('SELECT * FROM team_messages WHERE event_key=?',(key,)).fetchone()
        return {**dict(row),'data':json.loads(row['data'])}
    def emit(self,goal,sender,body,kind='event',target=None,data=None,key=None):
        self.migrate()
        with self.store.connect() as db:return self.insert(db,goal,sender,body,kind,target,data,key)
    def feed(self,goal,runs):
        messages=[]
        if self.exists():
            with self.store.connect() as db:
                messages=[{**dict(row),'data':json.loads(row['data'])} for row in db.execute('SELECT * FROM team_messages WHERE goal_id=? ORDER BY created_at,id',(goal['id'],))]
        # Legacy entries are clearly labelled execution records, never fabricated agent dialogue.
        known={m['event_key'] for m in messages}
        if 'goal:'+goal['id'] not in known:
            messages.append({'id':'legacy:'+goal['id'],'sender':'user','target':None,'kind':'goal','body':goal['title'],'data':{'acceptance':goal['acceptance']},'created_at':goal['created_at']})
        for run in runs:
            if run['goal_id']!=goal['id'] or 'result:'+run['id'] in known or 'start:'+run['id'] in known:continue
            summary=run.get('review_summary')
            messages.append({'id':'legacy:'+run['id'],'sender':run['role'] if summary else 'tack','target':None,'kind':'result' if summary else 'event','body':summary or f"{run['role']} 실행: {run['status']}",'data':{'run_id':run['id'],'status':run['status'],'source':'legacy_execution'},'created_at':run['updated_at']})
        return sorted(messages,key=lambda m:(m['created_at'],m['id']))
    def send(self,orch,key,payload):
        if not orch.available():raise BoardError('목표를 찾을 수 없습니다.',404)
        if not isinstance(payload,dict) or set(payload)-{'version','body','target','kind','client_id'}:raise BoardError('메시지 필드를 확인해주세요.')
        body=text(payload.get('body'),'메시지',10000);kind=payload.get('kind','note');target=payload.get('target')
        mention=re.match(r'^@([^\s]+)',body)
        if mention:
            names={value['name']:role for role,value in self.settings()['roles'].items()};names.update({role:role for role in self.settings()['roles']})
            resolved=names.get(mention[1])
            if not resolved:raise BoardError('멘션할 역할을 찾을 수 없습니다. 팀 설정의 이름을 확인해주세요.')
            if resolved and target not in {None,resolved}:raise BoardError('멘션과 전달 대상이 다릅니다.')
            if resolved:target=resolved
        if not isinstance(kind,str) or kind not in {'note','question','revision','review','answer'}:raise BoardError('메시지 목적을 확인해주세요.')
        if target is not None and (not isinstance(target,str) or target not in self.settings()['roles']):raise BoardError('전달할 역할을 확인해주세요.')
        if kind=='question' and target is None:raise BoardError('질문할 역할을 선택해주세요.')
        if kind in {'revision','review'} and (target is None or self.settings()['roles'][target]['kind'] not in ({'implement','plan'} if kind=='revision' else {'verify','assess'})):raise BoardError('요청 목적에 맞는 역할을 선택해주세요.')
        client=text(payload.get('client_id'),'client_id',100)
        if not re.fullmatch(r'[A-Za-z0-9-]+',client):raise BoardError('메시지 식별자를 확인해주세요.')
        self.migrate()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute('SELECT * FROM team_messages WHERE event_key=?',('user:'+key+':'+client,)).fetchone()
            if existing:
                if kind=='answer' and target is None:target=existing['target']
                if (existing['body'],existing['target'],existing['kind'])!=(body,target,kind):raise BoardError('같은 메시지 식별자로 다른 내용을 보낼 수 없습니다.',409)
                return {'message':{**dict(existing),'data':json.loads(existing['data'])},'duplicate':True}
            row=db.execute('SELECT * FROM goals WHERE id=?',(key,)).fetchone()
            if not row:raise BoardError('목표를 찾을 수 없습니다.',404)
            goal=json.loads(row['data'])
            if type(payload.get('version')) is not int or row['version']!=payload['version']:raise BoardError('목표가 변경됐습니다. 입력을 보존하고 새로고침해주세요.',409)
            execute=kind!='note'
            if execute:
                if goal.get('worker') or goal.get('pending_request'):raise BoardError('작업 실행 중입니다. 메모는 남길 수 있으며 새 실행 요청은 종료 후 전달해주세요.',409)
                if kind=='answer':
                    if goal['status']!='waiting_answer':raise BoardError('답변을 기다리는 목표가 아닙니다.',409)
                    target=goal['steps'][goal['next_step']]['role']
                elif goal.get('planning'):raise BoardError('계획을 먼저 생성하고 확인해주세요.',409)
                matching=[i for i,s in enumerate(goal['steps']) if s['role']==target]
                if not matching:raise BoardError('이 목표에 해당 역할의 작업이 없습니다.')
                if kind in {'revision','review'}:
                    orch.require_approval({**goal,'id':key},self.settings())
                for other in db.execute('SELECT id,data FROM goals WHERE id<>?',(key,)):
                    state=json.loads(other['data'])
                    if state.get('worker') and state.get('workspace')==goal['workspace']:raise BoardError('같은 프로젝트 폴더의 다른 작업이 실행 중입니다.',409)
                index=goal['next_step'] if kind=='answer' else matching[-1]
                # A direct review still respects recorded prerequisites.
                if kind=='review':
                    for dep in orch.store.detail(goal['steps'][index]['task_id'])['task']['blocked_by']:
                        if orch.store.detail(dep)['task']['status']!='done':raise BoardError('선행 구현 작업을 먼저 완료해주세요.',409)
                goal.update(pending_request={'kind':kind,'target':target,'body':body,'index':index,'previous_status':goal['status'],'message_id':client},status='ready')
                if kind in {'revision','review','answer'}:goal.update(next_step=index,repair_note=body)
                if kind=='answer':goal['answers']=[*goal.get('answers',[]),{'questions':goal.get('questions',[]),'answer':body}]
                db.execute('UPDATE goals SET data=?,version=version+1,updated_at=? WHERE id=?',(json.dumps(goal,ensure_ascii=False),timestamp(),key))
            message=self.insert(db,key,'user',body,kind,target,{'execution_requested':execute},'user:'+key+':'+client)
        return {'message':message,'execute':execute,'version':row['version']+(1 if execute else 0)}

def validate_handoff(value):
    if value is None:return None
    if not isinstance(value,dict) or set(value)!={'target','kind','body','files'}:raise BoardError('전달 요청 형식을 확인해주세요.')
    if not isinstance(value['target'],str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}',value['target']) or value['kind'] not in {'revision','question','review'}:raise BoardError('전달 역할/목적을 확인해주세요.')
    text(value['body'],'전달 내용',4000)
    if not isinstance(value['files'],list) or len(value['files'])>50 or any(not isinstance(p,str) or not p or len(p)>1000 for p in value['files']):raise BoardError('전달 파일 범위를 확인해주세요.')
    return value

def conversation(orch,key):
    goal=orch.goal(key)
    if goal.get('worker') and time.time()-goal.get('heartbeat',0)>30:goal['status']='needs_recheck'
    tasks={t['id']:t for t in orch.store.all()}
    for step in goal['steps']:step['task_status']=tasks.get(step['task_id'],{}).get('status','unknown')
    with orch.store.connect() as db:runs=[orch.decode(row) for row in db.execute('SELECT * FROM executions WHERE goal_id=? ORDER BY created_at',(key,))]
    team=Team(orch.store)
    return {'goal':goal,'messages':team.feed(goal,runs),'runs':runs,'settings':team.settings()}


def skill_sources(workspace,config):
    result=[]
    for value in config.get('skills',[]):
        path=(Path(workspace)/value).expanduser().resolve() if not Path(value).expanduser().is_absolute() else Path(value).expanduser().resolve()
        if path.name!='SKILL.md' or not path.is_file() or path.stat().st_size>64000:raise BoardError('존재하는 64KB 이하 SKILL.md를 지정해주세요: '+value)
        result.append((str(path),path.read_text()))
    return result

def skill_inventory(workspace):
    roots=[Path(workspace)/'.agents/skills',Path(workspace)/'.codex/skills',Path.home()/'.agents/skills',Path(__import__('os').environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'skills']
    found=[]
    for root in roots:
        if root.is_dir():
            for path in sorted(root.glob('*/SKILL.md')):
                if path.is_file() and path.stat().st_size<=64000:found.append({'path':str(path.resolve()),'header':path.read_text()[:800]})
    return found[:80]
