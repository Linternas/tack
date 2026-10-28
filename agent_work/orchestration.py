"""Local goal execution and metadata-only lifecycle observations over the existing DB."""
import hashlib
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import tempfile
import time
import uuid
from .store import BoardError, Store, timestamp

from .tracking import Tracker, HOOKS, identifier, text

def commands(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 20:
        raise BoardError('검증 명령은 1~20개의 argv 배열이어야 합니다.')
    for argv in value:
        if not isinstance(argv, list) or not 1 <= len(argv) <= 100 or any(not isinstance(a, str) or not a or len(a) > 3000 or '\x00' in a for a in argv):
            raise BoardError('검증 명령은 shell 문자열이 아닌 argv 배열이어야 합니다.')
    return value

def validate_steps(steps):
    if not isinstance(steps,list) or not 1<=len(steps)<=30:
        raise BoardError('목표에는 1~30개 작업이 필요합니다.')
    for step in steps:
        if not isinstance(step,dict) or set(step)-{'title','prompt','role','files'}:
            raise BoardError('작업 필드를 확인해주세요.')
        text(step.get('title'),'step.title',200);text(step.get('prompt'),'step.prompt')
        if 'files' in step and (not isinstance(step['files'],list) or not 1<=len(step['files'])<=50 or any(not isinstance(name,str) or not name or len(name)>1000 for name in step['files'])):
            raise BoardError('작업 대상 파일 목록을 확인해주세요.')
        if not isinstance(step.get('role','implement'),str) or not __import__('re').fullmatch(r'[a-z][a-z0-9_]{0,39}',step.get('role','implement')):
            raise BoardError('역할은 plan/implement/verify 중 하나입니다.')
    return steps

class Orchestrator(Tracker):
    def __init__(self, store, workspace):
        self.store, self.workspace = store, str(Path(workspace).resolve())

    def migrate(self):
        with self.store.connect() as db:
            version = db.execute("SELECT value FROM metadata WHERE key='orchestration_schema'").fetchone()
        if version:
            if version[0] != '1':
                raise BoardError('지원하지 않는 실행 DB 버전입니다.')
            return
        self.store.backup()
        with self.store.connect() as db:
            db.executescript('''BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS goals (id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE, version INTEGER NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS executions (id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, task_id TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS executions_goal ON executions(goal_id);
                CREATE TABLE IF NOT EXISTS observations (id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT NOT NULL UNIQUE, thread_id TEXT NOT NULL, task_id TEXT, run_id TEXT, kind TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS thread_bindings (thread_id TEXT PRIMARY KEY, task_id TEXT NOT NULL);
                INSERT OR IGNORE INTO metadata VALUES ('orchestration_schema','1');
                COMMIT;''')

    def available(self):
        with self.store.connect() as db:
            return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='goals'").fetchone())

    @staticmethod
    def decode(row):
        return {**json.loads(row['data']), **{k:row[k] for k in row.keys() if k != 'data'}}

    def overview(self):
        if not self.available():
            return {'goals':[], 'runs':[], 'observations':[], 'capabilities':capabilities()}
        with self.store.connect() as db:
            goals=[self.decode(r) for r in db.execute('SELECT * FROM goals ORDER BY updated_at DESC')]
            runs=[self.decode(r) for r in db.execute('SELECT * FROM executions ORDER BY created_at DESC LIMIT 100')]
            events=[self.decode(r) for r in db.execute('SELECT * FROM observations ORDER BY id DESC LIMIT 100')]
        tasks={t['id']:t for t in self.store.all()}
        for goal in goals:
            for step in goal['steps']:
                step['task_status']=tasks.get(step['task_id'],{}).get('status','unknown')
            if goal.get('worker') and time.time()-goal.get('heartbeat',0)>30:
                goal['status']='needs_recheck'
        return {'goals':goals,'runs':runs,'observations':events,'capabilities':capabilities()}

    def goal(self, key):
        if not self.available():raise BoardError('목표를 찾을 수 없습니다.',404)
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM goals WHERE id=?',(key,)).fetchone()
        if not row:
            raise BoardError('목표를 찾을 수 없습니다.',404)
        return self.decode(row)

    def create(self, title, payload, *, planning=False):
        text(title,'title',200)
        if not isinstance(payload,dict) or set(payload)-{'steps','verification','acceptance'}:
            raise BoardError('목표 필드를 확인해주세요.')
        steps=validate_steps(payload.get('steps'))
        from .team import Team
        if any(step.get('role','implement') not in Team(self.store).settings()['roles'] for step in steps):raise BoardError('프로젝트 팀 설정에 등록된 역할을 사용해주세요.')
        verification=[] if planning and payload.get('verification')==[] else commands(payload.get('verification'))
        acceptance=text(payload.get('acceptance'),'acceptance')
        self.migrate()
        # Validate all input before creating task records. Each step depends on the preceding one.
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            root=self.store.write({'title':title,'category':'task','acceptance':acceptance},connection=db)['task']
            previous=[]; linked=[]
            for step in steps:
                task=self.store.write({'title':step['title'],'category':'task','blocked_by':previous,'summary':f"목표 {root['id']} / {step.get('role','implement')}"},connection=db)['task']
                linked.append({**step,'role':step.get('role','implement'),'task_id':task['id']});previous=[task['id']]
            data={'title':title,'workspace':self.workspace,'steps':linked,'verification':verification,'acceptance':acceptance,
                  'status':'ready','planning':planning,'next_step':0,'worker':'','heartbeat':0,'pause_requested':False,'verification_results':[], 'error':''}
            key=identifier('GOAL-');now=timestamp()
            db.execute('INSERT INTO goals VALUES (?,?,1,?,?,?)',(key,root['id'],json.dumps(data,ensure_ascii=False),now,now))
        return self.goal(key)

    def draft(self,title,payload):
        import shlex
        if not isinstance(payload,dict) or set(payload)-{'acceptance','verification','verification_text'}:raise BoardError('목표 입력 필드를 확인해주세요.')
        raw=payload.get('verification_text','')
        if not isinstance(raw,str):raise BoardError('검증 명령은 문자열이어야 합니다.')
        parsed=[shlex.split(line) for line in raw.splitlines() if line.strip()]
        if any(token in {'&&','||','|',';','>','<'} for argv in parsed for token in argv):raise BoardError('한 줄에 명령 하나씩 입력해주세요.')
        checks=payload.get('verification',parsed)
        acceptance=payload.get('acceptance','')
        if not isinstance(acceptance,str):raise BoardError('완료 조건은 문자열이어야 합니다.')
        briefing=not checks or not acceptance.strip()
        prompt=('Inspect the project read-only and propose the smallest ordered development plan. '
                'Use configured team roles or matching Crewset catalog roles. Include explicit workspace-relative files and a final verify-type role. '
                'Do not implement, run tests or spawn agents. Initial implementation requires separate user approval.')
        goal=self.create(title,{'acceptance':acceptance or title,'verification':checks,'steps':[{'title':'목표 해석·팀 추천·사용자 질문' if briefing else '프로젝트 확인 및 작업 계획','role':'plan','prompt':prompt}]},planning=True)
        return self.update(goal['id'],{'briefing':briefing,'supplied_acceptance':acceptance.strip(),'supplied_verification':checks},goal['version'])

    def fingerprints(self,files):
        result={};root=Path(self.workspace)
        for name in sorted(set(files)):
            path=(root/name).resolve()
            if Path(name).is_absolute() or not path.is_relative_to(root) or '.git' in Path(name).parts:
                raise BoardError('대상 파일은 프로젝트 내부 경로여야 합니다.')
            if not path.exists():result[name]='missing';continue
            if not path.is_file() or path.stat().st_size>10*1024*1024:
                raise BoardError('실행 근거 파일은 10MB 이하 일반 파일이어야 합니다.')
            digest=hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda:stream.read(65536),b''):digest.update(chunk)
            result[name]=digest.hexdigest()
        return result

    def line_fingerprints(self,files):
        result={}
        for name in files:
            path=(Path(self.workspace)/name).resolve()
            if not path.is_file():continue
            try:lines=path.read_text().splitlines(keepends=True)
            except UnicodeError:continue
            if len(lines)<=3000:
                result[name]=[hashlib.sha256(line.encode()).hexdigest() for line in lines]
        return result

    def line_changes(self,before):
        from difflib import SequenceMatcher
        current=self.line_fingerprints(before)
        result={}
        for name,lines in before.items():
            result[name]=[{'kind':tag,'before_lines':[i+1,j],'current_lines':[k+1,l]} for tag,i,j,k,l in SequenceMatcher(None,lines,current.get(name,[]),autojunk=False).get_opcodes()]
        return result

    def accept_plan(self,key,worker,steps,brief=None):
        self.check_workspace(self.goal(key))
        from .team import Team, skill_sources
        team=Team(self.store);settings=team.settings()
        from .crewset import merge_roles, catalog
        validate_steps(steps)
        profiles={r['id']:r['config'] for r in catalog()['roles']}
        requested={item['id']:{k:v for k,v in item.items() if k!='id'} for item in brief['roles']} if brief else {step['role']:profiles[step['role']] for step in steps if step['role'] not in settings['roles'] and step['role'] in profiles}
        snapshot=self.goal(key).get('execution_settings',{})
        if snapshot and snapshot['version']!=settings['version']:raise BoardError('계획 생성 중 팀 설정이 변경됐습니다. 최신 설정을 확인하고 계획을 다시 생성해주세요.',409)
        roles=merge_roles(settings['roles'],requested)
        if any(step['role'] not in roles for step in steps) or roles[steps[-1]['role']].get('kind',steps[-1]['role'])!='verify':raise BoardError('등록된 역할 및 최종 검증 역할이 필요합니다.')
        if brief:
            if len(requested)!=len(brief['roles']):raise BoardError('추천 역할 ID가 중복됐습니다.')
            text(brief['summary'],'summary',4000);text(brief['acceptance'],'acceptance');commands(brief['verification'])
            for role,config in requested.items():skill_sources(self.workspace,{**roles[role],'skills':list(dict.fromkeys(roles[role].get('skills',[])+config.get('skills',[])))})
        if brief or requested:team.migrate()
        files=[name for step in steps for name in step.get('files',[])]
        if not files:raise BoardError('생성된 계획에 대상 파일이 필요합니다.')
        self.fingerprints(files)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM goals WHERE id=?',(key,)).fetchone();data=json.loads(row['data'])
            if data.get('worker')!=worker or not data.get('planning'):
                raise BoardError('계획 소유권이 변경됐습니다.',409)
            if brief or requested:team.save({**settings,'roles':roles},connection=db)
            if brief:
                data.update(acceptance=data.get('supplied_acceptance') or brief['acceptance'],verification=data.get('supplied_verification') or brief['verification'],team_recommendation=brief['summary'],role_guidance={role:config.get('instructions','') for role,config in requested.items()},role_skills={role:config.get('skills',[]) for role,config in requested.items()})
                root=self.store.detail(row['task_id'])['task']
                self.store.write({'version':root['version'],'acceptance':data['acceptance'],'note':'목표 해석 완료; 추천 팀 및 계획 승인 대기'},row['task_id'],connection=db)
            linked=[data['steps'][0]];previous=[linked[0]['task_id']]
            for step in steps:
                task=self.store.write({'title':step['title'],'category':'task','blocked_by':previous,'summary':f"목표 {row['task_id']} / {step['role']}"},connection=db)['task']
                linked.append({**step,'task_id':task['id']});previous=[task['id']]
            data.update(steps=linked,next_step=1,planning=False,status='ready',questions=[],error='')
            db.execute('UPDATE goals SET data=?,version=version+1,updated_at=? WHERE id=?',(json.dumps(data,ensure_ascii=False),timestamp(),key))
        return self.goal(key)

    def update(self,key,change,version=None,worker=None):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM goals WHERE id=?',(key,)).fetchone()
            if not row: raise BoardError('목표를 찾을 수 없습니다.',404)
            current=json.loads(row['data'])
            if version is not None and (type(version) is not int or row['version']!=version):
                raise BoardError('목표가 변경됐습니다. 최신 상태를 확인해주세요.',409)
            if worker is not None and current.get('worker')!=worker:
                raise BoardError('다른 실행 관리자가 소유한 목표입니다.',409)
            if change.get('worker'):
                for other in db.execute('SELECT id,data FROM goals WHERE id<>?',(key,)):
                    state=json.loads(other['data'])
                    if state.get('worker') and state.get('workspace')==current['workspace']:
                        raise BoardError('같은 checkout에 실행 소유자가 있습니다.',409)
            current.update(change)
            db.execute('UPDATE goals SET data=?,version=version+?,updated_at=? WHERE id=?',(json.dumps(current,ensure_ascii=False),0 if set(change)=={'heartbeat'} else 1,timestamp(),key))
        return self.goal(key)

    def pause(self,key,version):
        goal=self.goal(key)
        if goal['status'] not in {'running','verifying'}:
            raise BoardError('진행 중인 목표만 중단할 수 있습니다.',409)
        return self.update(key,{'pause_requested':True},version)

    def run_update(self,key,**change):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT data FROM executions WHERE id=?',(key,)).fetchone()
            data=json.loads(row[0]);data.update(change)
            db.execute('UPDATE executions SET data=?,updated_at=? WHERE id=?',(json.dumps(data,ensure_ascii=False),timestamp(),key))

    def run(self,goal,step,mode="work"):
        key=identifier('EXEC-');now=timestamp()
        with self.store.connect() as db:
            old=next((json.loads(row['data']) for row in db.execute('SELECT data FROM executions WHERE goal_id=? AND task_id=? ORDER BY created_at DESC',(goal['id'],step['task_id'])) if json.loads(row['data']).get('mode','work')==mode),{})
            data={'mode':mode,'kind':goal.get('execution_settings',{}).get('roles',{}).get(step['role'],{}).get('kind',step['role']),'tokens':0,'model':goal.get('execution_settings',{}).get('roles',{}).get(step['role'],{}).get('model',''),'status':'running','role':step['role'],'adapter':'codex-exec','thread_id':old.get('thread_id',''),'event_count':0,'exit_code':None}
            db.execute('INSERT INTO executions VALUES (?,?,?,?,?,?)',(key,goal['id'],step['task_id'],json.dumps(data),now,now))
        return key,data

    def task_state(self,key,**change):
        current=self.store.detail(key)['task']
        return self.store.write({'version':current['version'],**change},key)

    def execute_role(self,goal,step,key,worker,question=False):
        from .team import Team, DEFAULTS, skill_sources, skill_inventory
        team=Team(self.store);run_id,run=self.run(goal,step,'question' if question else 'work')
        config={**goal['execution_settings']['roles'][step['role']]}
        config['instructions']=config['instructions'] or DEFAULTS['roles'].get(config.get('kind',step['role']),{}).get('instructions','')
        config['skills']=list(dict.fromkeys(config.get('skills',[])+goal.get('role_skills',{}).get(step['role'],[])))
        argv=['codex','-a','never','-s','read-only' if question or config.get('kind',step['role'])!='implement' else 'workspace-write','exec','--json','--skip-git-repo-check']
        if config['model']:argv+=['--model',config['model']]
        prompt=f"Tack task {step['task_id']}; role {step['role']}. Complete only the assigned development work. Do not commit, push, deploy, send external messages, or change Tack tracking yourself. Do not spawn other agents.\nGoal: {goal['title']}\nAcceptance: {goal['acceptance']}\nTask: {step['prompt']}"
        if step.get('files'):prompt+='\nApproved files for this step: '+json.dumps(step['files'])+'\nDo not edit outside these files. A request to change scope must be returned to the user, not implemented.'
        sources=skill_sources(goal['workspace'],config)
        if sources:
            self.run_update(run_id,skills=[{'path':path,'sha256':hashlib.sha256(content.encode()).hexdigest()} for path,content in sources])
            for path,content in sources:prompt+='\nExplicitly assigned skill '+path+':\n'+content
        if config['instructions']:prompt+='\nUser-configured role guidance: '+config['instructions']
        guidance=goal.get('role_guidance',{}).get(step['role'],'')
        if guidance and guidance not in config['instructions']:prompt+='\nGoal-specific role guidance: '+guidance
        if goal.get('baseline'):
            evidence={'before_file_sha256':goal['baseline'],'current_file_sha256':self.fingerprints(list(goal['baseline'])),'line_comparison':self.line_changes(goal.get('baseline_lines',{}))}
            prompt+='\nManager-collected pre-execution/current evidence: '+json.dumps(evidence)+'\nMatching hashes prove declared files unchanged. line_comparison is 1-based inclusive; equal ranges are identical before/current lines. Read current source to identify functions. Do not infer unchanged scope outside this evidence.'
        with self.store.connect() as db:
            previous={}
            for row in db.execute('SELECT task_id,data FROM executions WHERE goal_id=? ORDER BY created_at',(key,)):
                data=json.loads(row['data'])
                if data.get('mode')!='question' and data.get('review_summary'):previous[row['task_id']]={'role':data['role'],'summary':data['review_summary']}
        if previous:prompt+='\nActual prior role results: '+json.dumps(list(previous.values()),ensure_ascii=False)
        if goal.get('answers'):prompt+='\nUser answers (decisions to apply): '+json.dumps(goal['answers'],ensure_ascii=False)
        prompt+='\nConfigured team roles: '+json.dumps([{'id':role,'name':cfg['name'],'kind':cfg.get('kind',role),'model':cfg['model'],'skills':cfg.get('skills',[])} for role,cfg in goal['execution_settings']['roles'].items()],ensure_ascii=False)
        if goal.get('planning'):
            from .crewset import catalog
            profiles=catalog()
            prompt+='\nCrewset reusable role catalog: '+json.dumps([{'id':role['id'],'name':role['config']['name'],'kind':role['config']['kind'],'mission':role['mission']} for role in profiles['roles']],ensure_ascii=False)
            prompt+='\nPrefer existing configured roles or these Crewset role IDs when their responsibilities fit; never add all roles by default. Canonical Crewset responsibilities will be attached by the controller. Existing user role settings/models/skills are preserved. Use your task prompts for goal-specific assignments.'
        if goal.get('planning') and not question:
            argv+=['--output-schema',str(Path(__file__).parent/'assets/brief.schema.json')]
            prompt+='\nAct as the goal coordinator. Recommend only necessary roles (3-12, including plan, and at least one implement and one verify kind), models must be empty unless the user explicitly requested a model. Role kinds plan/implement/verify/assess determine capabilities: only implement writes files. Recommend skills ONLY from this installed inventory: '+json.dumps(skill_inventory(goal['workspace']),ensure_ascii=False)
            prompt+='\nA missing requested skill must be reported, never pretend it ran. Ask concise material product questions in questions; if questions exist, steps may be empty. Otherwise propose observable acceptance, real project verification argv (never echo/true/no-op), and ordered steps ending in a verify-kind role. All steps need explicit files. Include the plan role in roles. No implementation before separate approval.'
            prompt+='\nUser-supplied acceptance and verification commands must be preserved. Ask questions even when these are supplied. Separate verified facts, assumptions and decisions needed in summary.'
            prompt+='\nAutopilot='+str(goal['execution_settings']['autopilot'])+'. When enabled, choose and disclose reversible product assumptions; still ask the user about authority, irreversible actions or unresolved material scope. Never authorize remote actions.'
        else:
            argv+=['--output-schema',str(Path(__file__).parent/'assets/verdict.schema.json')]
            if config.get('kind',step['role'])=='plan' and not question:
                prompt+='\nThis is a planning-role task. Return passed=true when the assigned requirements, decisions or product plan have actually been produced and explained in summary. Do not implement or run tests. Lack of implementation is expected for this role and is not a reason for passed=false. This verdict completes only this planning step, never the overall goal.'
            else:
                prompt+='\nReturn passed=true only if this assigned step was actually performed; provide a concise factual summary without secrets. For implement roles, merely planning or waiting is passed=false. For assess/verify roles, report the actual evaluation verdict.'
        if question:
            prompt+='\nThis is a READ-ONLY QUESTION, not authorization to edit or execute a workflow. Answer the question in summary; passed means you answered. Set handoff=null. Do not change files, task states or run verification commands.\nQuestion: '+goal['pending_request']['body']
        elif not goal.get('planning'):
            prompt+='\nTack execution state: the user has ALREADY approved this plan through goal start. Any after-approval wording is satisfied. Execute this scoped step now; do not ask for the same approval again. No commits, pushes, deployments or external messages are authorized.'
            if goal.get('repair_note'):prompt+='\nPrevious verification feedback to address: '+goal['repair_note']
            prompt+='\nBlocking user decisions always require passed=false and a user/question handoff. A question takes precedence over passed=true. Only assess/verify roles may request revision of an earlier step; other roles must ask the user. If evaluation fails within the approved goal and files, set handoff={target:"configured role id from an earlier step",kind:"revision",body:"specific changes and recheck criteria",files:[approved relative paths]}. If a requirement, scope or authority decision is needed, target "user", kind "question". Otherwise handoff=null. Handoffs are requests for Tack to validate; never execute other agents yourself.'
        if run['thread_id']:argv+=['resume',run['thread_id']]
        argv+=['-']
        team.emit(key,'tack','질문을 전달했습니다.' if question else step['title']+' 작업을 전달했습니다.','handoff',step['role'],{'run_id':run_id,'task_id':step['task_id'],'files':step.get('files',[]),'source':'dispatch'},'start:'+run_id)
        result=self.process(argv,goal['workspace'],key,worker,run_id,prompt)
        self.run_update(run_id,status=result['status'],exit_code=result['exit_code'],error=result.get('error',''))
        with self.store.connect() as db:data=json.loads(db.execute('SELECT data FROM executions WHERE id=?',(run_id,)).fetchone()[0])
        summary=data.get('review_summary')
        team.emit(key,step['role'] if summary else 'tack',summary or ('실행 중단' if result['status']=='interrupted' else '실행 '+result['status']),
                  'answer' if question else 'result','user' if question else None,{'run_id':run_id,'status':result['status'],'error':result.get('error',''),'files':step.get('files',[]),'tokens':data.get('tokens',0),'source':'agent_result' if summary else 'execution_event'},'result:'+run_id)
        return run_id,{**result,'handoff':data.get('handoff'),'summary':summary or ''}

    def check_workspace(self,goal):
        if Path(goal['workspace']).resolve()!=Path(self.workspace):
            raise BoardError('목표를 생성한 작업 폴더에서 실행해주세요. 다른 checkout의 실행·근거 수집을 차단했습니다.',409)

    def approval_contract(self,goal,policy):
        data={key:goal.get(key) for key in ('workspace','acceptance','verification','steps','role_guidance','role_skills')}
        data['roles']=policy['roles']
        return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

    def require_approval(self,goal,policy,approve=False):
        self.check_workspace(goal)
        request=goal.get('pending_request')
        if goal.get('planning') or request and request['kind']=='question':return
        if not approve and (not goal.get('approved_at') or goal.get('approved_contract')!=self.approval_contract(goal,policy)):
            raise BoardError('최신 계획과 팀 설정을 확인하고 goal start로 명시적으로 승인해주세요.',409)

    def work(self,key,version,*,approve=False):
        from .team import Team
        team=Team(self.store);self.migrate();goal=self.goal(key)
        if goal.get('worker'):raise BoardError('기존 실행 소유자가 있습니다. 상태 확인 후 재개하세요.',409)
        if goal['status'] not in {'ready','paused','failed','needs_recheck'}:raise BoardError('시작 또는 재개할 수 없는 목표입니다.',409)
        worker=identifier('WORKER-');policy=team.settings()
        self.require_approval(goal,policy,approve)
        if any(step['role'] not in policy['roles'] for step in goal['steps']):raise BoardError('목표에 사용된 역할이 팀 설정에서 제거됐습니다. 복원하거나 새 계획을 생성해주세요.')
        changes={'worker':worker,'heartbeat':time.time(),'status':'running','pause_requested':False,'error':'',
                 'execution_settings':policy,'auto_rounds':0,'token_used':0,'deadline_at':time.time()+policy['max_minutes']*60}
        index=goal['next_step'];request=goal.get('pending_request')
        if goal['status']=='failed' and not request and index<len(goal['steps']) and policy['roles'][goal['steps'][index]['role']]['kind'] in {'verify','assess'}:
            with self.store.connect() as db:
                row=db.execute('SELECT data FROM executions WHERE goal_id=? AND task_id=? ORDER BY created_at DESC LIMIT 1',(key,goal['steps'][index]['task_id'])).fetchone()
            review=json.loads(row['data']) if row else {}
            target=self.repair_target({**goal,'execution_settings':policy},index,review.get('handoff'))
            if target is None and not review.get('handoff'):
                candidates=[i for i in range(index) if policy['roles'][goal['steps'][i]['role']]['kind']=='implement']
                target=candidates[-1] if candidates else None
            if target is not None:
                feedback=review.get('handoff') or {}
                changes.update(next_step=target,repair_note=feedback.get('body') or review.get('review_summary',''))
        if not goal.get('planning') and not (request and request['kind']=='question'):
            changes['verification_results']=[]
            if approve:changes.update(approved_at=timestamp(),approved_contract=self.approval_contract(goal,policy))
        goal=self.update(key,changes,version)
        try:
            if request and request['kind']=='question':
                step=goal['steps'][request['index']]
                _,result=self.execute_role(goal,step,key,worker,question=True)
                self.update(key,{'status':request['previous_status'],'error':'' if result['status']=='succeeded' else result.get('error','질문 실패')},worker=worker)
                return
            self.task_state(goal['task_id'],status='in_progress',note='Tack 목표 실행 시작')
            files=[name for step in goal['steps'] for name in step.get('files',[])]
            if not goal.get('planning') and files and 'baseline' not in goal:
                if goal['next_step']>1:raise BoardError('실행 전 파일 근거를 과거 상태로 추정할 수 없습니다.')
                baseline=self.fingerprints(files);goal=self.update(key,{'baseline':baseline,'baseline_lines':self.line_fingerprints(baseline)},worker=worker)
            while self.goal(key)['next_step']<len(goal['steps']):
                goal=self.goal(key);index=goal['next_step'];step=goal['steps'][index]
                limit=self.budget_reason(goal)
                if goal['pause_requested'] or limit:
                    self.update(key,{'status':'paused','error':limit or 'interrupted'},worker=worker)
                    team.emit(key,'tack','실행을 중단했습니다. '+(limit or '사용자 중단 요청'),'pause',data={'reason':limit or 'user'})
                    return
                for dep in self.store.detail(step['task_id'])['task']['blocked_by']:
                    if self.store.detail(dep)['task']['status']!='done':raise BoardError('선행 작업이 완료되지 않았습니다.')
                self.task_state(step['task_id'],status='in_progress',note='Tack 역할 실행')
                run_id,result=self.execute_role(goal,step,key,worker)
                handoff=result.get('handoff')
                if result['status']!='succeeded' or handoff and handoff['target']=='user':
                    self.task_state(step['task_id'],status='needs_recheck',note='실행 중단/실패. QA 통과 아님')
                    handoff=result.get('handoff');target=self.repair_target(goal,index,handoff)
                    if handoff:
                        team.emit(key,step['role'],handoff['body'],'request',handoff['target'],{'files':handoff['files'],'run_id':run_id},'handoff:'+run_id)
                    current=self.goal(key)
                    if policy['autopilot'] and target is not None and current['auto_rounds']<policy['max_rounds'] and not current['pause_requested'] and not self.budget_reason(current):
                        goal=self.update(key,{'next_step':target,'repair_note':handoff['body'],'auto_rounds':current['auto_rounds']+1},worker=worker)
                        team.emit(key,'tack','승인된 범위의 재작업 요청을 자동 실행합니다.','decision',handoff['target'],{'round':goal['auto_rounds'],'files':handoff['files'],'source':'autopilot'})
                        continue
                    reason=result.get('error','실행 실패')
                    if handoff and handoff['target']!='user' and target is None:reason='scope_review_required'
                    elif policy['autopilot'] and target is not None and current['auto_rounds']>=policy['max_rounds']:reason='round_limit'
                    self.update(key,{'status':'waiting_answer' if handoff and handoff['target']=='user' else 'paused' if result['status']=='interrupted' else 'failed','error':reason,'questions':[{'id':'decision','question':handoff['body']}] if handoff and handoff['target']=='user' else []},worker=worker)
                    self.task_state(goal['task_id'],status='needs_recheck',note='실행 중단 또는 검토 필요')
                    team.emit(key,'tack','사용자 확인이 필요합니다.','decision','user',{'reason':reason,'source':'controller'})
                    return
                self.task_state(step['task_id'],status='done',resolution='역할 실행 종료. 목표 완료는 별도 검증 후 판정.',note=f'실행 {run_id}')
                if goal.get('planning'):
                    brief=result.get('brief')
                    if brief and brief.get('questions'):
                        self.update(key,{'status':'waiting_answer','questions':brief['questions']},worker=worker)
                        self.task_state(goal['task_id'],status='waiting_decision',note='사용자 답변 대기')
                        self.task_state(step['task_id'],status='waiting_decision',note='기획자의 질문에 대한 사용자 답변 대기')
                        team.emit(key,step['role'],brief['summary']+'\n\n'+'\n'.join(q['question'] for q in brief['questions']),'question','user',{'questions':brief['questions'],'source':'coordinator'})
                        return
                    ready=self.accept_plan(key,worker,result.get('plan_steps'),brief)
                    team.emit(key,'tack','작업 계획이 준비됐습니다. 범위와 검증 명령을 확인하고 승인하세요.','plan','user',{'steps':ready['steps'],'verification':ready['verification'],'roles':{role:{**cfg,'skills':list(dict.fromkeys(cfg.get('skills',[])+ready.get('role_skills',{}).get(role,[]))),'instructions':cfg.get('instructions','')+'\n'+ready.get('role_guidance',{}).get(role,'')} for role,cfg in team.settings()['roles'].items()},'recommendation':ready.get('team_recommendation',''),'source':'plan_result'},'plan:'+run_id)
                    return
                self.update(key,{'next_step':index+1},worker=worker)
            self.update(key,{'status':'verifying','verification_results':[]},worker=worker);checks=[]
            for argv in goal['verification']:
                state=self.goal(key)
                if state['pause_requested'] or self.budget_reason(state):
                    self.update(key,{'status':'paused','error':self.budget_reason(state) or 'interrupted'},worker=worker);return
                result=self.process(argv,goal['workspace'],key,worker)
                checks.append({'argv':argv,'status':result['status'],'exit_code':result['exit_code']})
                self.update(key,{'verification_results':checks},worker=worker)
                team.emit(key,'tack','검증 '+('통과' if result['status']=='succeeded' else '실패'),'verification',data={'check':checks[-1],'source':'test_runner'})
                if result['status']!='succeeded':
                    self.update(key,{'status':'paused' if result['status']=='interrupted' else 'failed','error':result.get('error') or '검증 미완료 또는 실패'},worker=worker);return
            self.task_state(goal['task_id'],status='done',resolution='역할 작업 및 명시된 검증 통과',verification=json.dumps(checks,ensure_ascii=False),note='Tack 목표 검증 완료')
            self.update(key,{'status':'done'},worker=worker)
            team.emit(key,'tack','작업과 명시된 검증이 완료됐습니다.','decision','user',{'verification':checks,'source':'controller'})
        except BaseException as error:
            with self.store.connect() as db:
                for row in db.execute('SELECT id,data FROM executions WHERE goal_id=?',(key,)).fetchall():
                    data=json.loads(row['data'])
                    if data.get('status')=='running':
                        data.update(status='failed',error='관리자 예외로 실행 종료');db.execute('UPDATE executions SET data=?,updated_at=? WHERE id=?',(json.dumps(data),timestamp(),row['id']))
            self.update(key,{'status':'needs_recheck','error':str(error)[:500]},worker=worker)
            team.emit(key,'tack','실행을 멈췄습니다. 상태를 확인해주세요.','decision','user',{'reason':str(error)[:500]})
            raise
        finally:self.update(key,{'worker':'','heartbeat':time.time(),'pending_request':None},worker=worker)

    @staticmethod
    def budget_reason(goal):
        if goal.get('deadline_at') and time.time()>=goal['deadline_at']:return 'time_limit'
        if goal.get('execution_settings') and goal.get('token_used',0)>=goal['execution_settings']['max_tokens']:return 'token_limit'
        return ''

    @staticmethod
    def repair_target(goal,index,handoff):
        roles=goal.get('execution_settings',{}).get('roles',{})
        kind=roles.get(goal['steps'][index]['role'],{}).get('kind',goal['steps'][index]['role'])
        if kind not in {'verify','assess'} or not handoff or handoff['kind']!='revision' or handoff['target']=='user':return None
        if not handoff['files']:return None
        candidates=[i for i in range(index) if goal['steps'][i]['role']==handoff['target'] and set(handoff['files'])<=set(goal['steps'][i].get('files',[]))]
        return candidates[-1] if candidates else None

    def process(self,argv,cwd,goal_id,worker,run_id=None,prompt=None):
        # Only this worker's process group is ever signalled; never kill a PID from saved state.
        verdict_required=False
        if run_id:
            with self.store.connect() as db:
                row=db.execute('SELECT data,task_id FROM executions WHERE id=?',(run_id,)).fetchone()
                run_data=json.loads(row['data']);role=run_data.get('role');run_task=row['task_id']
                verdict_required=not self.goal(goal_id).get('planning') or run_data.get('mode')=='question'
        planning=bool(run_id and self.goal(goal_id).get('planning') and run_data.get('mode')!='question')
        plan_steps=None;brief=None
        verdict=None
        input_file=tempfile.TemporaryFile() if prompt is not None else None
        try:
            if input_file:
                input_file.write(prompt.encode());input_file.seek(0)
            proc=subprocess.Popen(argv,cwd=cwd,stdin=input_file if input_file else subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)
        finally:
            if input_file:input_file.close()
        reader=None;stopped=threading.Event()
        try:
            lines=queue.Queue(maxsize=1000)
            def read():
                for line in iter(proc.stdout.readline,b''):
                    while not stopped.is_set():
                        try:lines.put(line,timeout=.1);break
                        except queue.Full:pass
                    if stopped.is_set():return
                while not stopped.is_set():
                    try:lines.put(None,timeout=.1);return
                    except queue.Full:pass
            reader=threading.Thread(target=read,daemon=True)
            interrupted=False;interrupted_at=0;thread_id='';count=0;completed=False;failed=False;last=0;ended=False;error_code=''
            reader.start()
            while not ended or proc.poll() is None:
                if time.monotonic()-last>0.5:
                    state=self.goal(goal_id)
                    self.update(goal_id,{'heartbeat':time.time()},worker=worker);last=time.monotonic()
                    limit=self.budget_reason(state)
                    # A completed response may cross the token cap; stop before the next role without losing its result.
                    if limit=='token_limit' and completed:limit=''
                    if (state['pause_requested'] or limit) and not interrupted:
                        interrupted=True;interrupted_at=time.monotonic();error_code=limit or 'interrupted'
                        try:os.killpg(proc.pid,signal.SIGTERM)
                        except ProcessLookupError:pass
                    if interrupted and proc.poll() is None:
                        try:proc.wait(timeout=2)
                        except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL)
                if interrupted and time.monotonic()-interrupted_at>=2:
                    try:os.killpg(proc.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                try:line=lines.get(timeout=0.1)
                except queue.Empty:continue
                if line is None:ended=True;continue
                if not run_id:continue
                try:event=json.loads(line)
                except (ValueError,UnicodeError):continue
                count+=1
                item=event.get('item',{})
                if planning and event.get('type')=='item.completed' and isinstance(item,dict) and item.get('type')=='agent_message':
                    try:
                        result=json.loads(item.get('text',''))
                        if self.goal(goal_id).get('briefing') or isinstance(result,dict) and 'questions' in result:
                            brief=validate_brief(result)
                            plan_steps=brief['steps']
                        else:
                            plan_steps=validate_steps(result.get('steps'))
                            from .crewset import catalog
                            roles={**{r['id']:r['config'] for r in catalog()['roles']},**self.goal(goal_id)['execution_settings']['roles']}
                            if roles.get(plan_steps[-1]['role'],{}).get('kind',plan_steps[-1]['role'])!='verify' or any(not step.get('files') for step in plan_steps):raise BoardError('최종 검증과 파일 범위가 필요합니다.')
                    except (ValueError,TypeError,AttributeError,BoardError):plan_steps=None;brief=None
                if verdict_required and event.get('type')=='item.completed' and isinstance(item,dict) and item.get('type')=='agent_message':
                    try:
                        result=json.loads(item.get('text',''))
                        if isinstance(result,dict) and type(result.get('passed')) is bool and isinstance(result.get('summary'),str):
                            verdict=result['passed']
                            handoff=result.get('handoff')
                            from .team import validate_handoff
                            handoff=validate_handoff(handoff)
                            self.run_update(run_id,verdict=verdict,review_summary=result['summary'],handoff=handoff)
                    except (ValueError,TypeError,BoardError):verdict=False
                if event.get('type')=='thread.started':
                    thread_id=event.get('thread_id','')
                    if isinstance(thread_id,str) and thread_id:
                        self.bind(thread_id,run_task)
                if event.get('type')=='turn.completed':
                    completed=True
                    usage=event.get('usage',{})
                    tokens=sum(usage.get(k,0) for k in ('input_tokens','output_tokens') if type(usage.get(k,0)) is int and usage.get(k,0)>=0) if isinstance(usage,dict) else 0
                    run_data['tokens']=run_data.get('tokens',0)+tokens
                    self.run_update(run_id,tokens=run_data['tokens'])
                    state=self.goal(goal_id);self.update(goal_id,{'token_used':state.get('token_used',0)+tokens},worker=worker)
                if event.get('type') in {'turn.failed','error'}:
                    failed=True
                    info=event.get('error',{})
                    message=info.get('message','') if isinstance(info,dict) else ''
                    error_code='server_overloaded' if 'capacity' in message.lower() else 'codex_execution_failed'
                self.run_update(run_id,event_count=count,thread_id=thread_id or self.run_thread(run_id),last_event=event.get('type','unknown'))
            code=proc.wait()
            status='interrupted' if interrupted else ('succeeded' if code==0 and (not run_id or completed and not failed and (not verdict_required or verdict is True) and (not planning or bool(plan_steps) or bool(brief and brief['questions']))) else 'failed')
            if verdict_required and status=='failed' and code==0:error_code='review_rejected_or_missing_verdict' if run_data.get('kind',role) in {'verify','assess'} else 'planning_incomplete' if run_data.get('kind',role)=='plan' else 'implementation_incomplete'
            return {'status':status,'exit_code':code,'error':error_code or ('interrupted' if interrupted else 'codex_execution_failed' if run_id and code!=0 else 'invalid_plan' if planning and status=='failed' and not plan_steps else ''),'plan_steps':plan_steps,'brief':brief}
        finally:
            try:os.killpg(proc.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            if proc.poll() is None:
                try:proc.wait(timeout=2)
                except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
            try:os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            stopped.set()
            if reader is not None and reader.ident is not None:reader.join(timeout=2)
            proc.stdout.close()

    def run_thread(self,key):
        with self.store.connect() as db:
            return json.loads(db.execute('SELECT data FROM executions WHERE id=?',(key,)).fetchone()[0]).get('thread_id','')

    def reconcile(self,key,version):
        goal=self.goal(key)
        if goal.get('worker') and time.time()-goal.get('heartbeat',0)<30:
            raise BoardError('실행 관리자가 아직 응답하고 있습니다.',409)
        if goal.get('worker'):
            # A missing heartbeat cannot prove the child exited. Human confirmation is mandatory.
            raise BoardError('실행 관리자의 응답이 없습니다. 기존 실행 종료를 확인한 뒤 recover --confirmed-stopped 를 사용하세요.',409)
        return self.update(key,{'status':'needs_recheck'},version)

    def recover(self,key,version,confirmed):
        goal=self.goal(key)
        if not confirmed or (goal.get('worker') and time.time()-goal.get('heartbeat',0)<30):
            raise BoardError('기존 실행 종료 확인 및 만료된 관리자 상태가 필요합니다.',409)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM goals WHERE id=?',(key,)).fetchone()
            current=json.loads(row['data'])
            if type(version) is not int or row['version']!=version or (current.get('worker') and time.time()-current.get('heartbeat',0)<30):
                raise BoardError('목표 상태가 변경됐습니다.',409)
            for run in db.execute('SELECT id,data FROM executions WHERE goal_id=?',(key,)).fetchall():
                data=json.loads(run['data'])
                if data.get('status')=='running':
                    data['status']='interrupted';db.execute('UPDATE executions SET data=?,updated_at=? WHERE id=?',(json.dumps(data),timestamp(),run['id']))
            current.update(worker='',status='paused',error='기존 실행 종료를 확인한 뒤 복구됨')
            db.execute('UPDATE goals SET data=?,version=version+1,updated_at=? WHERE id=?',(json.dumps(current),timestamp(),key))
        return self.goal(key)

def capabilities():
    import shutil
    return {'codex-exec':{'available':bool(shutil.which('codex')),'control':'Tack이 시작한 실행의 시작·중단·재개','observation':'JSON 실행 이벤트'},
            'codex-app':{'control':'기존 앱 세션 제어 미지원','observation':'설치된 Hook 신뢰 및 실제 수신 확인 필요'},
            'orca':{'available':bool(shutil.which('orca')),'control':'기존 Orca 실행 제어 미지원','observation':'계정별 Codex Hook 신뢰 및 실제 수신 확인 필요'}}

from .tracking import hook_command


def validate_brief(value):
    if not isinstance(value,dict) or set(value)!={'summary','roles','questions','acceptance','verification','steps'}:raise BoardError('목표 해석 응답 필드를 확인해주세요.')
    text(value['summary'],'summary',4000)
    questions=value['questions']
    if not isinstance(questions,list) or len(questions)>8:raise BoardError('질문은 8개 이하입니다.')
    for q in questions:
        if not isinstance(q,dict) or set(q)!={'id','question'}:raise BoardError('질문 형식을 확인해주세요.')
        text(q['id'],'질문 ID',80);text(q['question'],'질문',2000)
    if not isinstance(value['roles'],list) or not 3<=len(value['roles'])<=12:raise BoardError('추천 팀은 3~12개 역할이어야 합니다.')
    for role in value['roles']:
        if not isinstance(role,dict) or set(role)!={'id','kind','name','model','instructions','skills'}:raise BoardError('추천 역할 형식을 확인해주세요.')
        if not isinstance(role['id'],str) or not __import__('re').fullmatch(r'[a-z][a-z0-9_]{0,39}',role['id']) or role['kind'] not in {'plan','implement','verify','assess'}:raise BoardError('추천 역할 ID/유형을 확인해주세요.')
        if not isinstance(role['instructions'],str) or len(role['instructions'])>6000:raise BoardError('추천 역할 지침은 6000자 이하입니다.')
        if not isinstance(role['skills'],list) or len(role['skills'])>10 or any(not isinstance(v,str) or not v.strip() or len(v)>2000 for v in role['skills']):raise BoardError('추천 스킬은 경로 목록이어야 합니다.')
    if not questions:
        validate_steps(value['steps']);commands(value['verification']);text(value['acceptance'],'acceptance')
    return value
