"""Project employees, owned jobs and validated requests. One runner per checkout."""
import hashlib
import json
from pathlib import Path
import shlex
import time
from .store import BoardError, timestamp
from .orchestration import Orchestrator, commands, identifier, text
from .team import Team, skill_sources
from .usage import Usage

STATES = {'queued':'todo','working':'in_progress','waiting_reply':'blocked','waiting_user':'waiting_decision',
          'awaiting_approval':'blocked','awaiting_review':'blocked','done':'done','failed':'needs_recheck','paused':'blocked','conflict':'waiting_decision','waiting_jobs':'blocked'}


def paths(values, workspace):
    if not isinstance(values,list) or len(values)>50:
        raise BoardError('파일은 프로젝트 상대 경로 목록이어야 합니다.')
    root=Path(workspace).resolve()
    for value in values:
        if not isinstance(value,str) or not value or len(value)>1000 or Path(value).is_absolute() or any(c in value for c in '*?[]'):
            raise BoardError('구체적인 프로젝트 상대 파일 경로가 필요합니다.')
        target=(root/value).resolve()
        if not target.is_relative_to(root) or target==root or any(part in {'.git','.tack-orchestration','.agent-work','node_modules','.venv','venv'} for part in Path(value).parts) or target.is_dir():
            raise BoardError('프로젝트 안의 일반 파일만 업무 범위로 지정할 수 있습니다.')
    return list(dict.fromkeys(values))


def validate_result(value):
    keys={'action','summary','target','body','tasks','acceptance','verification','verdict'}
    if not isinstance(value,dict) or set(value)!=keys or not isinstance(value['action'],str) or value['action'] not in {'complete','ask','propose','review'}:
        raise BoardError('직원 응답 형식을 확인해주세요.')
    text(value['summary'],'summary')
    if value['target'] is not None and not isinstance(value['target'],str):raise BoardError('수신자를 확인해주세요.')
    if not isinstance(value['body'],str) or len(value['body'])>10000 or not isinstance(value['acceptance'],str) or len(value['acceptance'])>30000:
        raise BoardError('응답 본문을 확인해주세요.')
    if not isinstance(value['tasks'],list) or len(value['tasks'])>20 or not isinstance(value['verification'],list):raise BoardError('제안 업무를 확인해주세요.')
    if value['verdict'] is not None and not isinstance(value['verdict'],str):raise BoardError('검토 판정을 확인해주세요.')
    if value['verdict'] not in {None,'approved','changes_requested'}:raise BoardError('검토 판정을 확인해주세요.')
    if value['action']!='ask' and (value['target'] is not None or value['body'].strip()):raise BoardError('수신자와 질문 본문은 ask 응답에만 지정해주세요.')
    if value['action']=='ask' and (not value['target'] or not value['body'].strip()):raise BoardError('질문에는 수신자와 본문이 필요합니다.')
    if value['action']=='propose':
        if not value['tasks']:raise BoardError('제안할 업무가 필요합니다.')
        commands(value['verification']);text(value['acceptance'],'acceptance')
    elif value['tasks'] or value['verification'] or value['acceptance']:
        raise BoardError('업무 제안에서만 범위와 검증을 제시할 수 있습니다.')
    if value['action']=='review' and not value['verdict']:raise BoardError('검토 판정이 필요합니다.')
    if value['action']!='review' and value['verdict'] is not None:raise BoardError('검토 외 응답의 판정은 비워주세요.')
    return value


class Workforce:
    def __init__(self,store,workspace):
        self.store=store;self.workspace=str(Path(workspace).resolve());self.orch=Orchestrator(store,workspace);self.team=Team(store,workspace)

    def migrate(self):
        self.orch.migrate();self.team.migrate()
        with self.store.connect() as db:
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='team_jobs'").fetchone()
        if exists:return
        self.store.backup()
        with self.store.connect() as db:
            db.executescript('''BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS team_jobs (id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS team_jobs_goal ON team_jobs(goal_id,created_at);
                CREATE TABLE IF NOT EXISTS employee_sessions (goal_id TEXT NOT NULL, employee TEXT NOT NULL, capability TEXT NOT NULL, config_hash TEXT NOT NULL, thread_id TEXT NOT NULL, PRIMARY KEY(goal_id,employee,capability));
                COMMIT;''')

    def goal(self,key):
        goal=self.orch.goal(key)
        if goal.get('mode')!='team':raise BoardError('직원 팀 방식의 목표가 아닙니다.',409)
        self.orch.check_workspace(goal)
        return goal

    @staticmethod
    def decode(row):return {**json.loads(row['data']),**{k:row[k] for k in ('id','goal_id','version','created_at','updated_at')}}

    def jobs(self,key):
        with self.store.connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='team_jobs'").fetchone():return []
            return [self.decode(r) for r in db.execute('SELECT * FROM team_jobs WHERE goal_id=? ORDER BY created_at,id',(key,))]

    def job(self,key,job_id):
        result=next((j for j in self.jobs(key) if j['id']==job_id),None)
        if result is None:raise BoardError('이 목표의 업무를 찾을 수 없습니다.',404)
        return result

    def insert(self,db,key,*,title,employee,instructions,files=None,depends_on=None,kind='task',status='awaiting_approval',parent=None,depth=0,channel='team'):
        if not isinstance(employee,str) or employee not in self.team.settings()['roles']:raise BoardError('등록된 직원을 선택해주세요.')
        text(title,'title',200);text(instructions,'instructions')
        files=paths(files or [],self.workspace)
        if kind not in {'task','plan','question','review'} or status not in STATES:raise BoardError('업무 유형을 확인해주세요.')
        if self.team.settings()['roles'][employee]['kind']=='implement' and kind=='task' and not files:raise BoardError('구현 업무에는 승인할 파일 범위가 필요합니다.')
        if db.execute('SELECT COUNT(*) FROM team_jobs WHERE goal_id=?',(key,)).fetchone()[0]>=200:raise BoardError('목표당 업무 200개 한도입니다.')
        task=self.store.write({'title':title,'category':'task','owner':employee,'detail':instructions,'blocked_by':depends_on or [],'summary':'팀 목표 '+key,'status':STATES[status]},connection=db)['task']
        data=dict(title=title,employee=employee,instructions=instructions,files=files,depends_on=depends_on or [],kind=kind,status=status,parent=parent,depth=depth,channel=channel,replies=[],result='',question='',revision=0,approved_hash=None)
        now=timestamp();db.execute('INSERT INTO team_jobs VALUES (?,?,1,?,?,?)',(task['id'],key,json.dumps(data,ensure_ascii=False),now,now))
        return task['id']

    def change(self,db,job_id,change,version=None):
        row=db.execute('SELECT * FROM team_jobs WHERE id=?',(job_id,)).fetchone()
        if not row:raise BoardError('업무를 찾을 수 없습니다.',404)
        if version is not None and (type(version) is not int or row['version']!=version):raise BoardError('업무가 변경됐습니다. 최신 상태를 확인해주세요.',409)
        old=json.loads(row['data']);data={**old,**change}
        if data['status'] not in STATES:raise BoardError('업무 상태를 확인해주세요.')
        task=self.store.detail(job_id)['task']
        payload={'version':task['version'],'status':STATES[data['status']],'note':'직원 업무: '+data['status']}
        if data['status']=='done':payload['resolution']=data.get('result') or '담당 업무 결과 확인'
        self.store.write(payload,job_id,connection=db)
        db.execute('UPDATE team_jobs SET data=?,version=version+1,updated_at=? WHERE id=?',(json.dumps(data,ensure_ascii=False),timestamp(),job_id))

    def create(self,title,acceptance='',verification_text=''):
        text(title,'title',200)
        if not isinstance(acceptance,str) or len(acceptance)>30000 or not isinstance(verification_text,str):raise BoardError('목표 내용을 확인해주세요.')
        verification=commands([shlex.split(line) for line in verification_text.splitlines() if line.strip()]) if verification_text.strip() else []
        self.migrate();key=identifier('GOAL-');now=timestamp()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            root=self.store.write({'title':title,'category':'task','acceptance':acceptance or title},connection=db)['task']
            data=dict(mode='team',title=title,workspace=self.workspace,steps=[],next_step=0,planning=False,acceptance=acceptance or title,supplied_acceptance=acceptance,verification=verification,supplied_verification=verification,status='ready',worker='',heartbeat=0,pause_requested=False,verification_results=[],error='',auto_rounds=0,token_used=0)
            db.execute('INSERT INTO goals VALUES (?,?,1,?,?,?)',(key,root['id'],json.dumps(data,ensure_ascii=False),now,now))
            self.insert(db,key,title='목표 구체화와 업무 제안',employee='plan',instructions='사용자의 목표를 구체화하세요. 중요한 질문은 사용자에게, 기술 질문은 담당 동료에게 보내세요. 질문이 해결되면 담당 직원·파일·의존성·완료 조건·실제 검증 명령을 제안하세요. 구현은 승인 전 시작하지 마세요.',kind='plan',status='queued')
            self.team.insert(db,key,'user',title,'goal',data={'acceptance':acceptance},key='goal:'+key)
        return self.goal(key)

    def contract(self,job,roles):
        role=roles.get(job['employee'])
        if not role:raise BoardError('담당 직원이 제거됐습니다. 팀 설정을 확인해주세요.',409)
        # Changes to a skill also invalidate approval and the employee conversation.
        skills=[(p,hashlib.sha256(content.encode()).hexdigest()) for p,content in skill_sources(self.workspace,role)]
        return hashlib.sha256(json.dumps([role,job['instructions'],job['files'],skills],sort_keys=True,ensure_ascii=False).encode()).hexdigest()

    def idle_goal(self,key,version):
        goal=self.goal(key)
        if type(version) is not int or goal['version']!=version or goal.get('worker'):raise BoardError('실행 종료 후 최신 목표 상태에서 처리해주세요.',409)
        if goal['status']=='done':raise BoardError('완료된 목표에는 새 업무를 추가할 수 없습니다.',409)
        return goal

    def approve(self,key,version):
        goal=self.goal(key)
        if type(version) is not int or goal['version']!=version or goal['status']=='done':raise BoardError('최신 목표 상태를 확인해주세요.',409)
        roles=self.team.settings()['roles'];jobs=self.jobs(key)
        pending=[j for j in jobs if j['status']=='awaiting_approval']
        if not pending:raise BoardError('승인할 업무가 없습니다.',409)
        if goal.get('worker') and any(roles.get(j['employee'])!=goal['execution_settings']['roles'].get(j['employee']) for j in pending):raise BoardError('새 직원·설정의 업무는 현재 실행이 끝난 뒤 승인해주세요.',409)
        hashes={j['id']:self.contract(j,roles) for j in pending}
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.lock_goal(db,key,version,goal.get('worker') or None)
            for job in pending:self.change(db,job['id'],{'status':'queued','approved_hash':hashes[job['id']]},job['version'])
            self.team.insert(db,key,'user','제안된 업무·파일 범위와 검증 명령을 승인했습니다.','decision',data={'jobs':[j['id'] for j in pending],'verification':goal['verification']})
            self.set_goal(db,key,status='running' if goal.get('worker') else 'ready',error='')
        return self.snapshot(key)

    def assign(self,key,payload):
        allowed={'version','title','employee','instructions','files','depends_on','client_id'}
        if not isinstance(payload,dict) or set(payload)!=allowed:raise BoardError('업무 배정 필드를 확인해주세요.')
        goal=self.goal(key)
        if type(payload['version']) is not int or goal['version']!=payload['version'] or goal['status']=='done':raise BoardError('최신 목표 상태를 확인해주세요.',409)
        deps=payload['depends_on']
        if not isinstance(deps,list) or any(not isinstance(d,str) or d not in {j['id'] for j in self.jobs(key)} for d in deps):raise BoardError('같은 목표의 선행 업무를 지정해주세요.')
        client=text(payload['client_id'],'client_id',100)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');self.lock_goal(db,key,payload['version'],goal.get('worker') or None)
            event='assign:'+key+':'+client
            if db.execute('SELECT 1 FROM team_messages WHERE event_key=?',(event,)).fetchone():raise BoardError('이미 배정된 요청입니다. 새로고침해주세요.',409)
            job_id=self.insert(db,key,title=payload['title'],employee=payload['employee'],instructions=payload['instructions'],files=payload['files'],depends_on=deps)
            self.team.insert(db,key,'user',payload['instructions'],'assignment',payload['employee'],{'job_id':job_id},event)
            self.set_goal(db,key,status='running' if goal.get('worker') else 'awaiting_approval',verification_results=[])
        return self.snapshot(key)

    def reply(self,key,job_id,payload):
        if not isinstance(payload,dict) or set(payload)!={'version','body'}:raise BoardError('최신 업무 버전과 답변이 필요합니다.')
        text(payload['body'],'body',10000);self.goal(key);job=self.job(key,job_id)
        if job['status']!='waiting_user':raise BoardError('사용자 답변을 기다리는 업무가 아닙니다.',409)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            root=db.execute('SELECT data FROM goals WHERE id=?',(key,)).fetchone()
            self.change(db,job_id,{'status':'queued','replies':[*job['replies'],{'from':'user','question':job['question'],'body':payload['body']}],'question':''},payload['version'])
            self.team.insert(db,key,'user',payload['body'],'answer',job['employee'],{'job_id':job_id,'channel':job.get('channel','team')})
            self.set_goal(db,key,status='running' if json.loads(root[0]).get('worker') else 'ready',verification_results=[])
        return self.snapshot(key)

    @staticmethod
    def lock_goal(db,key,version=None,worker=None):
        row=db.execute('SELECT version,data FROM goals WHERE id=?',(key,)).fetchone();data=json.loads(row['data'])
        if version is not None and (type(version) is not int or row['version']!=version) or worker is not None and data.get('worker')!=worker or worker is None and data.get('worker'):
            raise BoardError('목표 상태가 변경됐습니다. 최신 상태를 확인해주세요.',409)
        return data

    @staticmethod
    def set_goal(db,key,**changes):
        row=db.execute('SELECT data FROM goals WHERE id=?',(key,)).fetchone();data=json.loads(row[0]);data.update(changes)
        db.execute('UPDATE goals SET data=?,version=version+1,updated_at=? WHERE id=?',(json.dumps(data,ensure_ascii=False),timestamp(),key))

    def snapshot(self,key):
        goal=self.goal(key);jobs=self.jobs(key);roles=self.team.settings()['roles']
        with self.store.connect() as db:runs=[self.orch.decode(r) for r in db.execute('SELECT * FROM executions WHERE goal_id=? ORDER BY created_at',(key,))]
        employees=[]
        for employee,profile in roles.items():
            owned=[j for j in jobs if j['employee']==employee]
            active=next((j for j in owned if j['status']=='working'),None)
            pending=[j for j in owned if j['status'] not in {'done'}]
            state='working' if active else 'conflict' if any(j['status']=='conflict' for j in pending) else 'waiting_jobs' if any(j['status']=='waiting_jobs' for j in pending) else 'waiting_user' if any(j['status']=='waiting_user' for j in pending) else 'waiting_reply' if any(j['status']=='waiting_reply' for j in pending) else 'awaiting_approval' if any(j['status']=='awaiting_approval' for j in pending) else 'queued' if pending else 'idle'
            employees.append({'id':employee,**profile,'state':state,'current_job':active['id'] if active else pending[0]['id'] if pending else None,'open_jobs':len(pending),'done_jobs':sum(j['status']=='done' for j in owned)})
        active={j['employee'] for j in jobs if j['status']=='working'};done={j['id'] for j in jobs if j['status']=='done'}
        for job in jobs:
            job['wait_reason']='선행 업무를 기다립니다.' if job['status']=='queued' and not set(job['depends_on'])<=done else '담당 직원이 다른 업무를 진행 중입니다.' if job['status']=='queued' and job['employee'] in active else '빈 실행 슬롯을 기다립니다.' if job['status']=='queued' else '동료의 답변을 기다립니다.' if job['status']=='waiting_reply' else '위임한 업무가 끝나기를 기다립니다.' if job['status']=='waiting_jobs' else '사용자의 답변을 기다립니다.' if job['status']=='waiting_user' else '파일 범위 승인을 기다립니다.' if job['status']=='awaiting_approval' else '병합 충돌을 확인해주세요.' if job['status']=='conflict' else ''
        from .run_queue import overview
        queue=overview(self.store,key)
        scheduler={'capacity':(goal.get('execution_settings',{}) if goal.get('worker') else self.team.settings()).get('max_parallel',3),'running':sum(j['status']=='working' for j in jobs),'queued':sum(j['status']=='queued' for j in jobs),'waiting':sum(j['status'] in {'waiting_reply','waiting_jobs','waiting_user','awaiting_approval','awaiting_review','conflict'} for j in jobs)}
        return {'queue':queue,'scheduler':scheduler,'goal':goal,'jobs':jobs,'employees':employees,'messages':self.team.feed(goal,runs),'settings':self.team.settings(),'runs':runs}

    def employee_run(self,goal,job,worker):
        role=goal['execution_settings']['roles'][job['employee']]
        write=role['kind']=='implement' and job['kind']=='task'
        capability=('read:'+job.get('channel','team')+':'+job['employee']) if job['kind']=='question' and not job['parent'] else ('write' if write else 'read')+':'+job['id'];signature=self.contract(job,goal['execution_settings']['roles'])
        sources=skill_sources(self.workspace,role)
        profile_hash=hashlib.sha256(json.dumps([role,[(p,hashlib.sha256(c.encode()).hexdigest()) for p,c in sources]],sort_keys=True).encode()).hexdigest()
        with self.store.connect() as db:
            old=db.execute('SELECT thread_id,config_hash FROM employee_sessions WHERE goal_id=? AND employee=? AND capability=?',(goal['id'],job['employee'],capability)).fetchone()
        # Reuse only this employee, goal and capability; a goal's answers never become another goal's instructions.
        thread=old[0] if old and old[1]==profile_hash else ''
        step={'task_id':job['id'],'role':job['employee']}
        run_id,_=self.orch.run(goal,step,'question' if job['kind']=='question' else 'work')
        self.orch.run_update(run_id,thread_id=thread,job_id=job['id'],employee=job['employee'],execution_workspace=job.get('execution_workspace',self.workspace))
        instructions=('You are a project employee. Work only on your assigned job. The user is the final decision maker. '
            'Never commit, push, deploy, send external messages, modify Tack records, spawn agents or broaden permissions. '
            'This is an isolated copy, not a Git worktree: do not run Git commands. Re-read the current files on every dispatch. The controller preserves pending changes and owns task binding/checkpoints. '
            'Coordinate through your structured result; the controller validates and delivers requests. '
            'Never claim a colleague ran unless its actual result is supplied. Only implement task jobs may edit approved files; all other jobs are read-only. '
            'Use action ask with target user for material product/scope/authority decisions; use a colleague id for a concrete question. '
            'When a needed employee or skill is not configured, ask the user to add it rather than pretending it exists. '
            'Use propose to recommend owned jobs with explicit files and dependencies (zero-based earlier task indexes), acceptance and real verification argv. Proposals require user approval. '
            'Work in your isolated execution directory; never write the original project or another employee workspace. Return questions without losing partial work. '
            'Use complete when this job is actually performed. For review jobs use review and verdict approved or changes_requested. '
            'Only review jobs decide review verdicts. A question job answers only; it cannot propose work or edit. '
            'Return the JSON schema exactly. Unused strings are empty, lists empty and target/verdict null. Preserve full results and explain unexecuted checks.\n')
        prompt=instructions+'Employee profile: '+json.dumps(role,ensure_ascii=False)
        for path,content in sources:prompt+='\nAssigned skill '+path+':\n'+content
        prompt+='\nGoal: '+goal['title']+'\nAcceptance: '+goal['acceptance']
        prompt+='\nAssigned job: '+json.dumps({k:job[k] for k in ('id','title','instructions','files','kind','replies','result')},ensure_ascii=False)
        prompt+='\nColleagues: '+json.dumps([{'id':k,'name':v['name'],'responsibility':v['instructions'],'kind':v['kind']} for k,v in goal['execution_settings']['roles'].items()],ensure_ascii=False)
        if job['parent']:
            parent=self.job(goal['id'],job['parent'])
            prompt+='\nLinked colleague job and actual result: '+json.dumps({k:parent[k] for k in ('id','employee','title','instructions','files','result')},ensure_ascii=False)
        dependencies=[self.job(goal['id'],d) for d in job['depends_on']]
        if dependencies:prompt+='\nActual prerequisite results: '+json.dumps([{'id':j['id'],'employee':j['employee'],'result':j['result']} for j in dependencies],ensure_ascii=False)
        prompt+='\nApproved verification argv: '+json.dumps(goal['verification'])
        if thread:self.bind_thread(goal['id'],job['id'],job['employee'],thread,thread)
        argv=['codex','-c','sandbox_workspace_write.writable_roots=[]','-a','never','-s','workspace-write' if write else 'read-only','exec','--json','--skip-git-repo-check']
        if role['model']:argv+=['--model',role['model']]
        argv+=['--output-schema',str(Path(__file__).parent/'assets/employee.schema.json')]
        if thread:argv+=['resume',thread]
        argv+=['-']
        self.team.emit(goal['id'],'tack',job['title']+' 업무를 맡겼습니다.','dispatch',job['employee'],{'job_id':job['id'],'run_id':run_id},'start:'+run_id)
        started=time.monotonic();result=None
        try:result=self.orch.process(argv,job.get('execution_workspace',self.workspace),goal['id'],worker,run_id,prompt)
        finally:Usage(self.store).finish(run_id,result['status'] if result else 'failed',int((time.monotonic()-started)*1000))
        self.orch.run_update(run_id,status=result['status'],exit_code=result['exit_code'],error=result.get('error',''),skills=[{'path':p,'sha256':hashlib.sha256(c.encode()).hexdigest()} for p,c in sources])
        thread=self.orch.run_thread(run_id)
        if thread:
            with self.store.connect() as db:
                db.execute('INSERT INTO employee_sessions VALUES (?,?,?,?,?) ON CONFLICT(goal_id,employee,capability) DO UPDATE SET config_hash=excluded.config_hash,thread_id=excluded.thread_id',(goal['id'],job['employee'],capability,profile_hash,thread))
        return result

    @staticmethod
    def validate_action(job,result,roles):
        action=result['action']
        if job['kind']=='review' and action not in {'review','ask'}:raise BoardError('검토 업무는 판정 또는 질문으로 응답해야 합니다.')
        if job['kind']=='question' and action not in {'complete','ask'}:raise BoardError('동료 질문은 답변 또는 추가 질문만 가능합니다.')
        if action=='review' and job['kind']!='review':raise BoardError('검토 판정은 독립 검토 업무에서만 가능합니다.')
        if action=='ask' and result['target'] not in {*roles,'user'}:raise BoardError('질문할 직원을 찾을 수 없습니다.')
        if action=='ask' and result['target']==job['employee']:raise BoardError('자기 자신에게 질문을 배정할 수 없습니다.')
        if action=='ask' and (job['depth']>=8 or len(job['replies'])>=20):raise BoardError('협업 질문 한도에 도달했습니다. 사용자의 정리가 필요합니다.',409)

    def apply(self,key,job_id,result,worker):
        result=validate_result(result);job=self.job(key,job_id);goal=self.goal(key);roles=goal['execution_settings']['roles'];policy=goal['execution_settings']
        action=result['action']
        self.validate_action(job,result,roles)
        proposals=[]
        if action=='propose':
            for index,item in enumerate(result['tasks']):
                if not isinstance(item,dict) or set(item)!={'title','employee','instructions','files','depends_on'}:raise BoardError('제안 업무 필드를 확인해주세요.')
                if not isinstance(item['employee'],str) or item['employee'] not in roles:raise BoardError('등록된 직원에게 업무를 배정해주세요.')
                text(item['title'],'title',200);text(item['instructions'],'instructions')
                paths(item['files'],self.workspace)
                if not isinstance(item['depends_on'],list) or any(type(d) is not int or not 0<=d<index for d in item['depends_on']):raise BoardError('제안 업무의 의존성은 앞선 업무 번호여야 합니다.')
                if roles[item['employee']]['kind']=='implement' and not item['files']:raise BoardError('구현 파일 범위가 필요합니다.')
                proposals.append(item)
            if len(self.jobs(key))+len(proposals)>200:raise BoardError('목표당 최대 200개 업무까지 관리합니다.')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');self.lock_goal(db,key,worker=worker)
            self.team.insert(db,key,job['employee'],result['summary'],'result',data={'job_id':job_id,'action':action,'channel':job.get('channel','team')})
            if action=='ask':
                target=result['target']
                self.change(db,job_id,{'status':'waiting_user' if target=='user' else 'waiting_reply','question':result['body'],'result':result['summary']},job['version'])
                if target!='user':
                    self.insert(db,key,title='질문: '+job['title'][:190],employee=target,instructions=result['body'],files=job['files'],kind='question',status='queued',parent=job_id,depth=job['depth']+1)
                self.team.insert(db,key,job['employee'],result['body'],'question',target,{'job_id':job_id,'channel':job.get('channel','team') if target=='user' else 'team'})
            elif action=='propose':
                ids=[]
                for item in proposals:
                    deps=[ids[d] for d in item['depends_on']]
                    ids.append(self.insert(db,key,**{k:v for k,v in item.items() if k!='depends_on'},depends_on=deps))
                self.change(db,job_id,{'status':'done' if job['kind']=='plan' else 'waiting_jobs','delegated_to':ids,'result':result['summary']},job['version'])
                # A later proposal invalidates completed verification; each new scope still needs the user's approval.
                self.set_goal(db,key,acceptance=goal.get('supplied_acceptance') or result['acceptance'],verification=goal.get('supplied_verification') or result['verification'],verification_results=[])
                self.team.insert(db,key,job['employee'],'업무 배정과 파일 범위를 제안했습니다. 승인 후 실행할 수 있습니다.','proposal','user',{'jobs':ids,'acceptance':result['acceptance'],'verification':result['verification']})
            elif action=='review':
                parent=self.job(key,job['parent'])
                if result['verdict']=='approved':
                    self.change(db,parent['id'],{'status':'done'},parent['version'])
                else:
                    retry=policy['autopilot'] and goal.get('auto_rounds',0)<policy['max_rounds']
                    self.change(db,parent['id'],{'status':'queued' if retry else 'awaiting_approval','revision':parent['revision']+1,'replies':[*parent['replies'],{'from':job['employee'],'body':result['summary']}]},parent['version'])
                    self.set_goal(db,key,auto_rounds=goal.get('auto_rounds',0)+1,verification_results=[])
                    self.team.insert(db,key,job['employee'],result['summary'],'revision',parent['employee'],{'job_id':parent['id'],'automatic':retry})
                self.change(db,job_id,{'status':'done','result':result['summary']},job['version'])
            elif job['kind']=='question' and job['parent']:
                self.change(db,job_id,{'status':'done','result':result['summary']},job['version'])
                parent=self.job(key,job['parent'])
                if parent['status']!='waiting_reply':raise BoardError('원래 업무가 더 이상 답변 대기 중이 아닙니다.',409)
                self.change(db,parent['id'],{'status':'queued','replies':[*parent['replies'],{'from':job['employee'],'question':job['instructions'],'body':result['summary']}]},parent['version'])
                self.team.insert(db,key,job['employee'],result['summary'],'answer',parent['employee'],{'job_id':parent['id']})
            elif roles[job['employee']]['kind']=='implement' and job['kind']=='task':
                reviewer=next((k for k,v in roles.items() if v['kind']=='verify' and k!=job['employee']),None)
                if reviewer is None:raise BoardError('독립 검토 직원을 설정해주세요.')
                self.change(db,job_id,{'status':'awaiting_review','result':result['summary']},job['version'])
                self.insert(db,key,title='검토: '+job['title'][:190],employee=reviewer,instructions='연결된 구현 업무의 실제 파일과 완료 조건을 독립 검토하세요. 필요한 검증을 실행하고 통과 여부를 판정하세요. 결함은 구체적인 수정·재검증 조건과 함께 보고하세요.',files=job['files'],kind='review',status='queued',parent=job_id)
            else:self.change(db,job_id,{'status':'done','result':result['summary']},job['version'])

    def run(self,key,version):
        goal=self.goal(key);policy=self.team.settings()
        if goal.get('worker') or goal['status'] not in {'ready','queued','paused','failed','needs_recheck','awaiting_approval','waiting_answer'}:raise BoardError('현재 목표는 실행할 수 없습니다.',409)
        worker=identifier('WORKER-')
        goal=self.orch.update(key,{'worker':worker,'status':'running','heartbeat':time.time(),'pause_requested':False,'error':'','execution_settings':policy,'deadline_at':time.time()+policy['max_minutes']*60,'token_used':goal.get('token_used',0)},version)
        current_job=None
        try:
            self.orch.task_state(goal['task_id'],status='in_progress',note='직원 팀 업무 진행')
            from .scheduler import run_jobs
            halted=run_jobs(self,key,worker,policy)
            goal=self.goal(key)
            if halted:
                self.orch.update(key,{'status':'paused','error':halted},worker=worker);return
            jobs=self.jobs(key)
            if jobs and all(j['status']=='done' for j in jobs):
                if not goal['verification']:
                    self.orch.update(key,{'status':'paused','error':'최종 검증 명령이 없습니다. 기획 직원에게 검증 계획을 요청해주세요.'},worker=worker);return
                checks=[]
                for argv in self.goal(key)['verification']:
                    check=self.orch.process(argv,self.workspace,key,worker);checks.append({'argv':argv,**check})
                    self.orch.update(key,{'verification_results':checks},worker=worker)
                    if check['status']!='succeeded':
                        self.orch.update(key,{'status':'failed','error':'최종 검증 실패. 결과를 확인하고 직원에게 후속 업무를 배정해주세요.'},worker=worker);return
                self.orch.update(key,{'status':'awaiting_acceptance','error':''},worker=worker)
                self.team.emit(key,'tack','담당 업무와 검증이 끝났습니다. 결과를 확인하고 최종 완료를 승인해주세요.','decision','user',{'verification':checks})
            else:
                status='failed' if any(j['status']=='failed' for j in jobs) else 'waiting_answer' if any(j['status']=='conflict' for j in jobs) else 'awaiting_approval' if any(j['status']=='awaiting_approval' for j in jobs) else 'waiting_answer' if any(j['status']=='waiting_user' for j in jobs) else 'paused'
                self.orch.update(key,{'status':status,'error':'' if status!='paused' else '진행 가능한 업무가 없습니다. 의존성과 실패 업무를 확인해주세요.'},worker=worker)
        except Exception as error:
            if current_job:
                with self.store.connect() as db:self.change(db,current_job['id'],{'status':'failed','result':str(error)[:1000]})
            self.orch.update(key,{'status':'needs_recheck','error':str(error)[:1000]},worker=worker)
            raise
        finally:self.orch.update(key,{'worker':'','heartbeat':time.time()},worker=worker)

    def accept(self,key,version):
        goal=self.idle_goal(key,version);jobs=self.jobs(key)
        if goal['status']!='awaiting_acceptance' or not jobs or any(j['status']!='done' for j in jobs) or not goal['verification_results'] or any(c['status']!='succeeded' for c in goal['verification_results']):raise BoardError('모든 업무와 실제 검증이 완료돼야 합니다.',409)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');self.lock_goal(db,key,version)
            task=self.store.detail(goal['task_id'])['task']
            self.store.write({'version':task['version'],'status':'done','resolution':'사용자가 직원 업무 결과와 실제 검증을 확인하고 완료 승인','verification':json.dumps(goal['verification_results'],ensure_ascii=False)},goal['task_id'],connection=db)
            self.set_goal(db,key,status='done')
            self.team.insert(db,key,'user','작업 결과를 확인하고 목표 완료를 승인했습니다.','decision')
        return self.snapshot(key)

    def bind_thread(self,key,job_id,employee,thread,expected_thread):
        with self.store.connect() as db:row=db.execute('SELECT task_id FROM thread_bindings WHERE thread_id=?',(thread,)).fetchone()
        previous=row[0] if row else None
        if previous and previous!=job_id:
            if thread!=expected_thread or self.job(key,previous)['employee']!=employee:
                raise BoardError('이 직원의 승인된 대화가 아닌 세션 전환을 차단했습니다.',409)
        self.orch.bind(thread,job_id,previous if previous and previous!=job_id else None)

    def message(self,key,payload):
        required={'version','kind','employee','body','client_id'}
        if not isinstance(payload,dict) or not required<=set(payload) or set(payload)-required-{'channel'} or not isinstance(payload['kind'],str) or payload['kind'] not in {'note','question'} or not isinstance(payload['employee'],str):
            raise BoardError('메모 또는 직원 질문 형식을 확인해주세요.')
        goal=self.goal(key);text(payload['body'],'body',10000);text(payload['client_id'],'client_id',100)
        roles=self.team.settings()['roles'];channel=payload.get('channel','team');employee=payload['employee']
        if not isinstance(channel,str) or channel!='team' and (not channel.startswith('dm:') or channel[3:] not in roles):raise BoardError('대화방을 확인해주세요.')
        if channel!='team':
            if employee and employee!=channel[3:]:raise BoardError('개인 대화방과 수신자가 다릅니다.')
            employee=channel[3:]
        import re
        mention=re.match(r'^@([^\s]+)',payload['body'])
        if mention and payload['kind']=='question':
            names={**{r:r for r in roles},**{v['name']:r for r,v in roles.items()}};target=names.get(mention[1])
            if not target or employee and target!=employee:raise BoardError('멘션과 질문할 직원을 확인해주세요.')
            employee=target
        if payload['kind']=='question' and employee not in roles:raise BoardError('질문할 직원을 선택해주세요.')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            event='team-user:'+key+':'+payload['client_id']
            previous=db.execute('SELECT * FROM team_messages WHERE event_key=?',(event,)).fetchone()
            if previous:
                data=json.loads(previous['data'])
                if (previous['body'],previous['kind'],previous['target'],data.get('channel','team'))!=(payload['body'],payload['kind'],employee or None,channel):raise BoardError('같은 메시지 ID로 다른 내용을 보낼 수 없습니다.',409)
                return self.snapshot(key)
            row=db.execute('SELECT version,data FROM goals WHERE id=?',(key,)).fetchone()
            if goal['status']=='done':raise BoardError('완료된 목표에는 새 요청을 보낼 수 없습니다.',409)
            if type(payload['version']) is not int or row['version']!=payload['version']:raise BoardError('목표가 변경됐습니다. 입력을 보존하고 새로고침해주세요.',409)
            data={'channel':channel}
            if payload['kind']=='question':
                job_id=self.insert(db,key,title='질문: '+payload['body'][:190],employee=employee,instructions=payload['body'],kind='question',status='queued',channel=channel)
                data['job_id']=job_id
                if not json.loads(row['data']).get('worker'):self.set_goal(db,key,status='ready',verification_results=[])
            self.team.insert(db,key,'user',payload['body'],payload['kind'],employee or None,data,key=event)
        return self.snapshot(key)

    def retry(self,key,job_id,payload):
        if not isinstance(payload,dict) or set(payload)!={'version'}:raise BoardError('최신 업무 버전이 필요합니다.')
        goal=self.goal(key);job=self.job(key,job_id)
        if goal['status']=='done' or job['status'] not in {'failed','paused','conflict'}:raise BoardError('중단·실패한 업무만 실행 종료 후 재시도할 수 있습니다.',409)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');self.lock_goal(db,key,worker=goal.get('worker') or None)
            discard=job['status']=='conflict' or job.get('error_kind')=='scope'
            if discard:
                from .workspaces import Workspaces
                spaces=Workspaces(self.workspace,self.store.path);spaces.discard(spaces.folder(key,job_id))
            self.change(db,job_id,{'status':'queued','error_kind':'',**({'staged':None} if discard else {})},payload['version'])
            self.set_goal(db,key,status='running' if goal.get('worker') else 'ready',error='',verification_results=[])
            self.team.insert(db,key,'user','업무 재시도를 요청했습니다.','decision',job['employee'],{'job_id':job_id})
        return self.snapshot(key)
