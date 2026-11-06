"""CLI entry points shared with the optional dashboard."""
import json
import os
import subprocess
import sys
import threading
from .orchestration import Orchestrator, hook_command, capabilities
from .store import BoardError, Store


def add_parser(sub):
    settings=sub.add_parser('settings').add_subparsers(dest='settings_action',required=True)
    settings.add_parser('show')
    settings.add_parser('catalog')
    crew=settings.add_parser('crewset');crew.add_argument('--version',type=int,required=True)
    select=crew.add_mutually_exclusive_group();select.add_argument('--roles');select.add_argument('--from-project',action='store_true')
    settings.add_parser('update').add_argument('--json',dest='input',required=True)
    crew=sub.add_parser('team',help='직원 업무와 협업 관리').add_subparsers(dest='team_action',required=True)
    crew.add_parser('list')
    crew.add_parser('drain')
    crew.add_parser('collaboration').add_argument('id')
    for action in ('delegate','approve-request'):
        item=crew.add_parser(action);item.add_argument('id');item.add_argument('--json',dest='input',required=True)
    item=crew.add_parser('revoke-delegation');item.add_argument('id');item.add_argument('--version',type=int,required=True)
    create=crew.add_parser('create');create.add_argument('title');create.add_argument('--acceptance',default='');create.add_argument('--verification-text',default='');create.add_argument('--team-id')
    show=crew.add_parser('show');show.add_argument('id')
    for action in ('run','approve','accept'):
        item=crew.add_parser(action);item.add_argument('id');item.add_argument('--version',type=int,required=True)
    for action in ('assign','message'):
        item=crew.add_parser(action);item.add_argument('id');item.add_argument('--json',dest='input',required=True)
    for action in ('reply','retry'):
        item=crew.add_parser(action);item.add_argument('id');item.add_argument('job_id');item.add_argument('--json',dest='input',required=True)
    goal=sub.add_parser('goal').add_subparsers(dest='goal_action',required=True)
    goal.add_parser('list')
    for action in ('add','draft'):
        add=goal.add_parser(action);add.add_argument('title');add.add_argument('--json',dest='input',required=action=='add')
    goal.add_parser('messages').add_argument('id')
    send=goal.add_parser('send');send.add_argument('id');send.add_argument('--json',dest='input',required=True)
    for action in ('show','start','work','pause','reconcile','recover'):
        parser=goal.add_parser(action);parser.add_argument('id')
        if action!='show':parser.add_argument('--version',type=int,required=True)
        if action=='work':parser.add_argument('--approve',action='store_true')
        if action=='recover':parser.add_argument('--confirmed-stopped',action='store_true')


def start(project,key,version,*,approve=False):
    from .harness import require_enabled
    require_enabled(project['workspace'])
    orch=Orchestrator(Store(project['db_path']),project['workspace'])
    goal=orch.goal(key)
    if goal.get('mode')=='team':
        from .run_queue import enqueue
        return enqueue(project,key,version)
    allowed={'ready','paused','failed','needs_recheck'} | ({'awaiting_approval','waiting_answer'} if goal.get('mode')=='team' else set())
    if goal['version']!=version or goal.get('worker') or goal['status'] not in allowed:
        raise BoardError('최신 목표 상태를 확인해주세요.',409)
    from .team import Team
    orch.require_approval(goal,Team(orch.store).settings(),approve)
    env={**os.environ,'TACK_HARNESS':'1','PYTHONPATH':str(__import__('pathlib').Path(__file__).resolve().parent.parent)+os.pathsep+os.environ.get('PYTHONPATH','')}
    proc=subprocess.Popen([sys.executable,'-m','agent_work','--workspace',project['workspace'],'goal','work',key,'--version',str(version)]+(['--approve'] if approve else []),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,env=env)
    threading.Thread(target=proc.wait,daemon=True).start()
    return {'accepted':True,'goal_id':key,'worker_launcher_pid':proc.pid,'note':'실행 수락은 완료가 아닙니다. goal show로 실제 상태를 확인하세요.'}


def dispatch(args,project,store,payload):
    orch=Orchestrator(store,project['workspace'])
    if args.command=='doctor':
        data=orch.overview()
        return {'workspace':project['workspace'],'data_path':str(store.path),'capabilities':capabilities(),
                'received_hook_events':len(data['observations']), 'note':'설정 파일 존재만으로 Hook 동작이나 현재 sandbox 쓰기 권한을 보장하지 않습니다.'}
    if args.command=='bind':
        orch.bind(args.thread,args.task_id,args.expected_task);return {'thread_id':args.thread,'task_id':args.task_id}
    if args.command=='settings':
        from .team import Team
        team=Team(store,project['workspace'])
        if args.settings_action=='show':return team.settings()
        if args.settings_action=='catalog':
            from .crewset import catalog
            return catalog()
        if args.settings_action=='crewset':
            from .crewset import apply
            return apply(team,args.version,roles=[r.strip().replace('_','-') for r in args.roles.split(',')] if args.roles is not None else None,workspace=project['workspace'] if args.from_project else None)
        return team.save(payload(args.input))
    if args.command=='team':
        from .workforce import Workforce
        workforce=Workforce(store,project['workspace']);action=args.team_action
        if action=='drain':
            from .run_queue import drain
            return drain(store,project['workspace'])
        if action in {'collaboration','delegate','approve-request','revoke-delegation'}:
            from .collaboration import Collaboration
            collaboration=Collaboration(workforce)
            if action=='collaboration':return collaboration.snapshot(args.id)
            data={'version':args.version} if action=='revoke-delegation' else payload(args.input)
            result=getattr(collaboration,{'delegate':'save_policy','approve-request':'approve_request','revoke-delegation':'revoke'}[action])(args.id,data)
            if action!='revoke-delegation':result['launch']=collaboration.kick({**project,'_goal_id':args.id})
            return result
        if action=='list':return {'goals':[g for g in orch.overview()['goals'] if g.get('mode')=='team']}
        if action=='create':
            goal=workforce.create(args.title,args.acceptance,args.verification_text,team_id=getattr(args,'team_id',None))
            return {**goal,'launch':start(project,goal['id'],goal['version'])} if goal['execution_policy']['execution_mode']=='autopilot' else goal
        if action=='show':return workforce.snapshot(args.id)
        if action=='run':return start(project,args.id,args.version)
        if action=='approve':return workforce.approve(args.id,args.version)
        if action=='accept':return workforce.accept(args.id,args.version)
        if action=='assign':return workforce.assign(args.id,payload(args.input))
        if action=='message':return workforce.message(args.id,payload(args.input))
        if action=='reply':return workforce.reply(args.id,args.job_id,payload(args.input))
        if action=='retry':return workforce.retry(args.id,args.job_id,payload(args.input))
    action=args.goal_action
    if action in {'messages','send'}:
        from .team import Team,conversation
        if action=='messages':return conversation(orch,args.id)
        result=Team(store).send(orch,args.id,payload(args.input))
        if result.get('execute'):result['launch']=start(project,args.id,result['version'])
        return result
    if action=='list':return orch.overview()
    if action=='add':return orch.create(args.title,payload(args.input))
    if action=='draft':
        goal=orch.draft(args.title,{} if args.input is None else json.loads(args.input) if args.input.lstrip().startswith('{') else payload(args.input))
        return {**goal,'launch':start(project,goal['id'],goal['version'])}
    if action=='show':return orch.goal(args.id)
    if action=='start':return start(project,args.id,args.version,approve=True)
    if action=='pause':
        if orch.goal(args.id).get('mode')=='team':
            from .run_queue import cancel
            from .workforce import Workforce
            return cancel(Workforce(store,project['workspace']),args.id,args.version)
        return orch.pause(args.id,args.version)
    if action=='reconcile':return orch.reconcile(args.id,args.version)
    if action=='recover':
        result=orch.recover(args.id,args.version,args.confirmed_stopped)
        from .run_queue import wake
        wake(project)
        return result
    orch.work(args.id,args.version,approve=args.approve);return orch.goal(args.id)
