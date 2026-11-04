"""Bounded employee scheduling over persistent jobs; model calls happen only for runnable work."""
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from pathlib import Path
from .store import BoardError
from .workspaces import Workspaces,Conflict


def run_jobs(workforce,key,worker,policy):
    spaces=Workspaces(workforce.workspace,workforce.store.path)
    active={};dispatched=0;halted='';capacity=policy.get('max_parallel',3)

    def execute(goal,job):
        attempt=spaces.capture(key,job['id'],job.get('staged'))
        job={**job,'execution_workspace':attempt['root']}
        with workforce.store.connect() as db:workforce.change(db,job['id'],{'execution_workspace':attempt['root']})
        result=None
        try:
            result=workforce.employee_run(goal,job,worker)
            return result,attempt,spaces.archive(attempt)
        except BaseException:
            # Save partial work even if the process/collector fails before returning a verdict.
            record=spaces.archive(attempt)
            with workforce.store.connect() as db:workforce.change(db,job['id'],{'staged':record})
            raise

    def finish(future,job):
        record=None
        try:
            result,attempt,record=future.result()
            current=workforce.job(key,job['id'])
            changes=spaces.read_artifact(record)
            role=policy['roles'][job['employee']];write=role['kind']=='implement' and job['kind']=='task'
            outside=set(changes)-set(job['files'])
            if outside or not write and changes:
                with workforce.store.connect() as db:workforce.change(db,job['id'],{'status':'failed','staged':record,'error_kind':'scope','result':'승인 파일 밖 수정은 통합하지 않았습니다: '+', '.join(sorted(outside or changes)[:12])})
                return
            if result['status']!='succeeded' or not result.get('team_result'):
                with workforce.store.connect() as db:workforce.change(db,job['id'],{'status':'paused' if result['status']=='interrupted' else 'failed','staged':record,'result':result.get('error') or '직원 응답을 확인하지 못했습니다.'})
                return
            answer=result['team_result']
            workforce.validate_action(job,answer,policy['roles'])
            if answer['action'] in {'complete','review'}:
                spaces.integrate(attempt,record,job['files'],write)
                with workforce.store.connect() as db:workforce.change(db,job['id'],{'staged':None,'error_kind':''})
            else:
                with workforce.store.connect() as db:workforce.change(db,job['id'],{'staged':record})
            workforce.apply(key,job['id'],answer,worker)
        except Conflict as error:
            current=workforce.job(key,job['id'])
            retry_review=current['kind']=='review'
            with workforce.store.connect() as db:workforce.change(db,job['id'],{'status':'queued' if retry_review else 'conflict','result':str(error),'staged':record or current.get('staged'),'error_kind':'conflict'})
            workforce.team.emit(key,'tack','검토 기준이 바뀌어 다시 확인합니다.' if retry_review else str(error),'conflict',job['employee'],{'job_id':job['id']})
        except Exception as error:
            with workforce.store.connect() as db:workforce.change(db,job['id'],{'status':'failed','result':str(error)[:1000],**({'staged':record} if record else {})})

    with ThreadPoolExecutor(max_workers=capacity,thread_name_prefix='tack-employee') as pool:
        while True:
            goal=workforce.goal(key)
            limit=workforce.orch.budget_reason(goal)
            if goal.get('pause_requested') or limit:halted=limit or 'interrupted'
            jobs=workforce.jobs(key)
            done={j['id'] for j in jobs if j['status']=='done'}
            for job in jobs:
                if job['status']=='waiting_jobs' and set(job.get('delegated_to',[]))<=done:
                    results=[{'from':child['employee'],'body':child['result']} for child in jobs if child['id'] in job.get('delegated_to',[])]
                    with workforce.store.connect() as db:workforce.change(db,job['id'],{'status':'queued','replies':[*job['replies'],*results]},job['version'])
            busy={j['employee'] for j in active.values()}
            if not halted:
                for job in workforce.jobs(key):
                    if len(active)>=capacity:break
                    if dispatched>=100:halted='dispatch_limit';break
                    if job['status']!='queued' or job['employee'] in busy or not set(job['depends_on'])<=done:continue
                    if job['kind']=='task' and job['approved_hash']!=workforce.contract(job,policy['roles']):
                        with workforce.store.connect() as db:workforce.change(db,job['id'],{'status':'awaiting_approval'},job['version'])
                        continue
                    with workforce.store.connect() as db:workforce.change(db,job['id'],{'status':'working'},job['version'])
                    job=workforce.job(key,job['id']);active[pool.submit(execute,goal,job)]=job;busy.add(job['employee']);dispatched+=1
            if not active:break
            completed,_=wait(active,timeout=.1,return_when=FIRST_COMPLETED)
            for future in completed:finish(future,active.pop(future))
    return halted
