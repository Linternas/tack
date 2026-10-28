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
    if goal['version']!=version or goal.get('worker') or goal['status'] not in {'ready','paused','failed','needs_recheck'}:
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
    if action=='pause':return orch.pause(args.id,args.version)
    if action=='reconcile':return orch.reconcile(args.id,args.version)
    if action=='recover':return orch.recover(args.id,args.version,args.confirmed_stopped)
    orch.work(args.id,args.version,approve=args.approve);return orch.goal(args.id)
