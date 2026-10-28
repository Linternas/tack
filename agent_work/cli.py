"""Headless project/task/session management; all writes use Store."""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from .projects import by_id, git_value, projects, resolve
from .store import BoardError, Store


def emit(value):
    print(json.dumps(value,ensure_ascii=False,indent=2))


def payload(path):
    value=json.loads(sys.stdin.read() if path=='-' else Path(path).read_text())
    if not isinstance(value,dict):
        raise BoardError('JSON 객체가 필요합니다.')
    return value


def parser():
    p=argparse.ArgumentParser(prog='tack',description='프로젝트별 작업·세션 관리. Dashboard는 선택 사항입니다.')
    from . import __version__
    p.add_argument('--version',action='version',version='Tack '+__version__)
    p.add_argument('--workspace',default=str(Path.cwd()))
    p.add_argument('--project',help='전역 프로젝트 ID')
    sub=p.add_subparsers(dest='command',required=True)
    update=sub.add_parser('update',help='초기 설정을 재실행하지 않고 설치본 일괄 갱신');update.add_argument('--source',type=Path,help='개발용 로컬 Tack 소스 폴더')
    init=sub.add_parser('init'); init.add_argument('--local',action='store_true'); init.add_argument('--name'); init.add_argument('--link'); init.add_argument('--source-root',action='append')
    sub.add_parser('projects').add_argument('--all',action='store_true',help='보관 프로젝트 포함')
    project_actions=sub.add_parser('project').add_subparsers(dest='project_action',required=True)
    for action in ('show','archive','restore','delete'):
        item=project_actions.add_parser(action);item.add_argument('id',nargs='?')
        if action!='show':item.add_argument('--version',type=int,required=True)
        if action=='delete':
            item.add_argument('--confirm',required=True,help='삭제할 프로젝트 이름');item.add_argument('--include-backups',action='store_true')
    status=sub.add_parser('status'); status.add_argument('--all',action='store_true')
    task=sub.add_parser('task').add_subparsers(dest='action',required=True)
    add=task.add_parser('add'); add.add_argument('title'); add.add_argument('--category',default='task'); add.add_argument('--json',dest='input')
    show=task.add_parser('show'); show.add_argument('id')
    update=task.add_parser('update'); update.add_argument('id'); update.add_argument('--json',dest='input',required=True)
    start=task.add_parser('start'); start.add_argument('id'); start.add_argument('--version',required=True,type=int)
    check=sub.add_parser('checkpoint'); check.add_argument('task_id'); check.add_argument('--session'); check.add_argument('--json',dest='input',required=True)
    resume=sub.add_parser('resume'); resume.add_argument('task_id'); resume.add_argument('--session')
    sessions=sub.add_parser('sessions'); sessions.add_argument('--all',action='store_true')
    document=sub.add_parser('document').add_subparsers(dest='document_action',required=True)
    document.add_parser('list')
    show_document=document.add_parser('show'); show_document.add_argument('key')
    import_document=document.add_parser('import'); import_document.add_argument('path'); import_document.add_argument('--kind',default='work',choices=['work','session','legacy_global'])
    sub.add_parser('backup')
    dash=sub.add_parser('dashboard'); dash.add_argument('--port',type=int,default=8788); dash.set_defaults(allow_execution=False)
    if os.environ.get('TACK_HARNESS')=='1' and Path(__file__).with_name('harness.py').is_file():dash.add_argument('--allow-execution',action='store_true',help='이 프로젝트 하네스에서 명시적 실행 제어 허용')
    migrate=sub.add_parser('migrate'); migrate.add_argument('--board',type=Path,required=True)
    migrate.add_argument('--notes',type=Path,action='append',default=[]); migrate.add_argument('--active-note',type=Path,action='append',default=[])
    hook=sub.add_parser('hook');hook.add_argument('--json',dest='input',default='-')
    bind=sub.add_parser('bind');bind.add_argument('thread');bind.add_argument('task_id');bind.add_argument('--from-task',dest='expected_task')
    sub.add_parser('doctor')
    if os.environ.get('TACK_HARNESS')=='1' and Path(__file__).with_name('harness.py').is_file():
        from .orchestration_cli import add_parser
        add_parser(sub)
    return p


def run(args):
    if args.command=='update':
        from .update import update
        result=update(args.source);emit(result)
        if result.get('errors'):raise BoardError('일부 설치본 업데이트에 실패했습니다. 출력의 errors를 확인해주세요.',409)
        return
    if args.command=='hook':
        from .tracking import hook_command
        return emit(hook_command(payload(args.input)))
    if args.command=='projects':
        return emit(projects(args.all))
    if args.command=='project':
        from .project_lifecycle import preview, change
        project=by_id(args.id) if args.id else by_id(args.project) if args.project else resolve(args.workspace)
        if args.project_action=='show':return emit(preview(project))
        data={'version':args.version}
        if args.project_action=='delete':data.update(confirmation=args.confirm,include_backups=args.include_backups)
        return emit(change(project,args.project_action,data))
    project=by_id(args.project) if args.project else resolve(args.workspace,create=args.command in {'init','migrate'} or (args.command=='task' and args.action=='add') or (args.command=='goal' and args.goal_action in {'add','draft'}),
        local=getattr(args,'local',False),name=getattr(args,'name',None),link=getattr(args,'link',None),source_roots=getattr(args,'source_root',None))
    if args.command=='migrate':
        from .migration import migrate
        return emit(migrate(project,args.board,args.notes,args.active_note))
    readonly=(args.command=='settings' and args.settings_action in {'show','catalog'}) or args.command in {'status','resume','sessions','doctor'} or (args.command=='goal' and args.goal_action in {'list','show','messages'}) or (args.command=='document' and args.document_action!='import') or (args.command=='task' and args.action=='show')
    from .project_lifecycle import read
    if not readonly and args.command!='dashboard' and read(project['db_path'])['status']=='archived':raise BoardError('보관 프로젝트는 읽기 전용입니다. project restore로 복원해주세요.',409)
    store=Store(project['db_path'],readonly=readonly)
    if args.command in {'bind','doctor'}:
        from .tracking import Tracker
        tracker=Tracker(store,project['workspace'])
        if args.command=='bind':
            tracker.bind(args.thread,args.task_id,args.expected_task);return emit({'thread_id':args.thread,'task_id':args.task_id})
        return emit({'workspace':project['workspace'],'data_path':str(store.path),'received_hook_events':len(tracker.overview()['observations']),'orchestration':False})
    if args.command in {'goal','settings'}:
        from .harness import require_enabled
        require_enabled(project['workspace'])
        from .orchestration_cli import dispatch
        return emit(dispatch(args,project,store,payload))
    if args.command=='init': return emit(project)
    if args.command=='document':
        if args.document_action=='list': return emit({'documents':store.documents()})
        if args.document_action=='import': return emit(store.archive_document(args.path,args.kind))
        from .documents import source_document
        return emit(source_document(store,project,args.key if not args.key.startswith('DOC-') else 'archive:'+args.key))
    if args.command=='status':
        tasks=store.all()
        if not args.all: tasks=[t for t in tasks if t['status'] not in {'done','archived'}]
        return emit({'project':project,'tasks':[{k:t[k] for k in ('id','title','category','status','version','next_action','blocked_by')} for t in tasks],
                     'sessions':[{k:r[k] for k in ('id','task_id','status','branch','next_action','updated_at')} for r in store.sessions(args.all)]})
    if args.command=='task':
        if args.action=='show': return emit(store.detail(args.id))
        if args.action=='add': return emit(store.write({'title':args.title,'category':args.category,**(payload(args.input) if args.input else {})}))
        if args.action=='update': return emit(store.write(payload(args.input),args.id))
        task=store.detail(args.id)['task']
        if task['status'] in {'done','archived'}: raise BoardError('완료·보관된 작업은 명시적으로 상태를 수정한 뒤 시작하세요.')
        if any(store.detail(key)['task']['status']!='done' for key in task['blocked_by']): raise BoardError('미완료 선행 작업이 있습니다.')
        return emit(store.write({'version':args.version,'status':'in_progress','note':'작업 시작'},args.id))
    if args.command=='checkpoint':
        data=payload(args.input)
        data.setdefault('workspace',project['workspace'])
        data.setdefault('branch',git_value(project['workspace'],'branch','--show-current'))
        data.setdefault('thread_id',os.environ.get('CODEX_THREAD_ID',''))
        data.setdefault('agent','codex' if os.environ.get('CODEX_THREAD_ID') else 'cli')
        data['task_id']=args.task_id
        return emit(store.checkpoint(data,args.session))
    if args.command=='resume': return emit(store.resume(args.task_id,args.session))
    if args.command=='sessions': return emit({'sessions':store.sessions(args.all)})
    if args.command=='backup': return emit({'backup':str(store.backup())})
    if args.command=='dashboard':
        from .http import BoardServer
        harness=os.environ.get('TACK_HARNESS')=='1' and Path(__file__).with_name('harness.py').is_file()
        if harness:
            from .harness import require_enabled
            require_enabled(project['workspace'])
        if args.allow_execution and not harness:raise BoardError('실행 제어는 프로젝트 로컬 tack-orchestration으로 시작하세요.')
        server=BoardServer(('127.0.0.1',args.port),store,project,allow_execution=args.allow_execution,harness=harness)
        print(f'Tack: http://127.0.0.1:{server.server_port}/?project={project["id"]}',flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close()


def main():
    try: run(parser().parse_args())
    except (BoardError,OSError,ValueError,sqlite3.Error) as error:
        print(json.dumps({'error':str(error),'status':getattr(error,'status',None)},ensure_ascii=False),file=sys.stderr)
        raise SystemExit(1)
