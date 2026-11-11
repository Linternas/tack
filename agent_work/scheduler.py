"""Bounded scheduling; approved conditions and execution attempts gate every integration."""
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from .store import BoardError
from .workspaces import Workspaces,Conflict,state
from .collaboration import Collaboration,digest
from .execution_policy import job_reason,changes_reason,effective,mode


def run_jobs(workforce,key,worker,policy):
    spaces=Workspaces(workforce.workspace,workforce.store.path);collab=Collaboration(workforce);collab.ensure()
    active={};dispatched=0;halted='';capacity=policy.get('max_parallel',3)
    with workforce.store.connect() as db:delegation=collab.policy(db,key)
    if delegation and delegation['mode']=='delegated':capacity=min(capacity,delegation['max_parallel'])

    def execute(goal,job):
        collab.check_attempt(key,job['id'],job['attempt_id'],worker);collab.check_pins(key,job)
        attempt=spaces.capture(key,job['id'],job.get('staged'));attempt=collab.preview(key,job,attempt)
        job={**job,'execution_workspace':attempt['root']}
        with workforce.store.connect() as db:workforce.change(db,job['id'],{'execution_workspace':attempt['root']})
        try:
            if job['kind']=='integration':
                result={'status':'succeeded','exit_code':0,'team_result':{'action':'complete','summary':'컨트롤러의 실제 통합 검증을 수행했습니다.','target':None,'body':'','tasks':[],'acceptance':'','verification':[],'verdict':None}}
            else:result=workforce.employee_run(goal,job,worker)
            return result,attempt,spaces.archive(attempt)
        except BaseException:
            record=spaces.archive(attempt)
            with workforce.store.connect() as db:
                current=collab.job(db,key,job['id'])
                if current.get('attempt_id')==job['attempt_id']:workforce.change(db,job['id'],{'staged':record})
            raise

    def finish(future,job):
        record=None
        try:
            result,attempt,record=future.result();collab.check_attempt(key,job['id'],job['attempt_id'],worker)
            current=workforce.job(key,job['id']);collab.check_pins(key,current)
            changes=spaces.read_artifact(record)
            reason=changes_reason(changes,workforce.goal(key),workforce.team.settings())
            if reason:raise BoardError(reason,403)
            role=policy['roles'][job['employee']];write=workforce.can_write(job,role)
            outside=set(changes)-set(job['files'])
            if outside or not write and changes:raise BoardError('승인 파일 밖 수정은 통합하지 않았습니다: '+', '.join(sorted(outside or changes)[:12]))
            if result['status']!='succeeded' or not result.get('team_result'):
                with workforce.store.connect() as db:workforce.change(db,job['id'],{'status':'paused' if result['status']=='interrupted' else 'failed','staged':record,'result':result.get('error') or '직원 응답을 확인하지 못했습니다.'})
                return
            answer=result['team_result'];workforce.validate_action(job,answer,policy['roles'])
            if write:
                reason=job_reason(workforce.goal(key),current,workforce.team.settings(),workforce.workspace,automatic=current.get('automatic_approval',False)) or collab.allowed(key,current)
                if reason or current['approved_hash']!=workforce.contract(current,workforce.team.settings()['roles']):raise BoardError(reason or '실행 중 승인 계약이 바뀌었습니다.',409)
            with workforce.store.connect() as db:
                db.execute('BEGIN IMMEDIATE');collab.check_attempt(key,job['id'],job['attempt_id'],worker,db)
                workforce.change(db,job['id'],{'staged':record})
            action=answer['action']
            if action=='work_request':collab.request(key,job['id'],answer['collaboration'],job['attempt_id'],worker);return
            if action=='contract_review':collab.review_contract(key,current,answer['collaboration'],worker);return
            if action=='contract_proposal' and not current.get('verification'):
                with workforce.store.connect() as db:
                    p=collab.policy(db,key)
                    if not p or not p['verification']:raise BoardError('계약 검증 명령을 먼저 승인해주세요.')
                    workforce.change(db,job['id'],{'verification':p['verification']})
                current=workforce.job(key,job['id'])
            if action in {'complete','contract_proposal'} and current.get('verification'):
                collab.run_checks(key,current,attempt,worker)
                after=spaces.archive(attempt)
                if spaces.read_artifact(after)!=changes:raise BoardError('검증 명령이 업무 산출물을 변경했습니다. 결과를 확인해주세요.',409)
                current=workforce.job(key,job['id'])
            if action in {'complete','contract_proposal'} and write:
                progress=digest({'files':{name:state(Path(attempt['root'])/name) for name in job['files']},'checks':[{k:c.get(k) for k in ('argv','status','exit_code')} for c in current.get('checks',[])]})
                if current.get('revision',0) and current.get('last_verified_progress')==progress:raise BoardError('재시도에 새로운 진전 근거가 없습니다. 사용자 결정이 필요합니다.',409)
                with workforce.store.connect() as db:workforce.change(db,job['id'],{'last_verified_progress':progress})
            if action=='contract_proposal':collab.propose_contract(key,current,answer['collaboration'],attempt,record,worker);return
            with workforce.store.connect() as db:p=collab.policy(db,key)
            if action=='complete' and write and p and set(changes)&set(p['documents']):raise BoardError('공유 계약 변경은 계약 제안·소비자 검토를 거쳐야 합니다.',409)
            if action in {'complete','review'}:
                with workforce.store.connect() as db:
                    # Hold the attempt/goal write lock while integrating so a pause cannot race publication.
                    db.execute('BEGIN IMMEDIATE');collab.check_attempt(key,job['id'],job['attempt_id'],worker,db);collab.check_pins(key,current)
                    spaces.integrate(attempt,record,job['files'],write)
                    workforce.change(db,job['id'],{'staged':None,'error_kind':''})
            workforce.apply(key,job['id'],answer,worker)
        except Conflict as error:
            with workforce.store.connect() as db:
                current=collab.job(db,key,job['id'])
                if current.get('attempt_id')!=job['attempt_id'] or current['status']!='working' or workforce.goal(key).get('worker')!=worker:return
                retry_review=current['kind']=='review'
                workforce.change(db,job['id'],{'status':'queued' if retry_review else 'conflict','result':str(error),'staged':record or current.get('staged'),'error_kind':'conflict'})
            workforce.team.emit(key,'tack','검토 기준이 바뀌어 다시 확인합니다.' if retry_review else str(error),'conflict',job['employee'],{'job_id':job['id']})
        except Exception as error:
            with workforce.store.connect() as db:
                current=collab.job(db,key,job['id'])
                if current.get('attempt_id')!=job['attempt_id'] or current['status']!='working' or workforce.goal(key).get('worker')!=worker:return
                goal=workforce.goal(key)
                decision=any(term in str(error) for term in ('순환','협업 요청 한도','새로운 진전'))
                status='paused' if goal.get('pause_requested') else 'waiting_user' if decision else 'failed'
                workforce.change(db,job['id'],{'status':status,'result':str(error)[:1000],'question':str(error) if decision else current.get('question',''),'error_kind':'scope' if '승인 파일 밖' in str(error) else 'collaboration',**({'staged':record} if record else {})})

    with ThreadPoolExecutor(max_workers=capacity,thread_name_prefix='tack-employee') as pool:
        while True:
            collab.synchronize(key);workforce.auto_approve(key,worker);goal=workforce.goal(key)
            limit=workforce.orch.budget_reason(goal) or collab.budget(key)
            if goal.get('pause_requested') or limit:halted=limit or 'interrupted'
            jobs=workforce.jobs(key);done={j['id'] for j in jobs if j['status']=='done'}
            for job in jobs:
                if job['status']=='waiting_jobs' and set(job.get('delegated_to',[]))<=done:
                    results=[{'from':child['employee'],'body':child['result']} for child in jobs if child['id'] in job.get('delegated_to',[])]
                    with workforce.store.connect() as db:workforce.change(db,job['id'],{'status':'queued','replies':[*job['replies'],*results]},job['version'])
            busy={j['employee'] for j in active.values()};waiting_slot=False
            if not halted:
                for job in workforce.jobs(key):
                    if len(active)>=capacity:break
                    if dispatched>=100:halted='dispatch_limit';break
                    if job['status']!='queued' or job['employee'] in busy or not set(job['depends_on'])<=done:continue
                    reason=job_reason(goal,job,workforce.team.settings(),workforce.workspace,automatic=job.get('automatic_approval',False)) or collab.allowed(key,job) or collab.budget(key,job['employee'])
                    if reason and job['kind'] not in {'task','document'}:
                        with workforce.store.connect() as db:workforce.change(db,job['id'],{'status':'paused','result':reason},job['version'])
                        continue
                    if job['kind'] in {'task','document'} and (reason or job['approved_hash']!=workforce.contract(job,policy['roles'])):
                        with workforce.store.connect() as db:workforce.change(db,job['id'],{'status':'awaiting_approval','result':reason},job['version'])
                        continue
                    job=collab.claim(key,job['id'],worker)
                    if job is None:waiting_slot=True;continue
                    active[pool.submit(execute,goal,job)]=job;busy.add(job['employee']);dispatched+=1
            if not active:
                if waiting_slot and not halted:time.sleep(.2);continue
                break
            completed,_=wait(active,timeout=.1,return_when=FIRST_COMPLETED)
            for future in completed:finish(future,active.pop(future))
    collab.synchronize(key)
    return halted
