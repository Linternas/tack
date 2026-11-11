"""Headless project/task/session management; all writes use Store."""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from .projects import by_id, git_value, projects, resolve
from .store import BoardError, Store, CATEGORIES, STATUSES, TEXT_FIELDS


def emit(value):
    print(json.dumps(value,ensure_ascii=False,separators=(',',':')))


def fields(value, keys):
    return {key: value[key] for key in keys}


def write_result(result, full, key='task'):
    if full:
        return result
    keys = ('id', 'version', 'status', 'updated_at')
    if key == 'session':
        keys += ('task_id',)
    return {key: fields(result[key], keys)}


def positive(value):
    number=int(value)
    if not 1<=number<=100:
        raise argparse.ArgumentTypeError('1~100 사이의 개수가 필요합니다.')
    return number


def offset(value):
    number=int(value)
    if number<0:raise argparse.ArgumentTypeError('0 이상의 위치가 필요합니다.')
    return number


def choices(value, allowed):
    selected = set(value.split(','))
    if not selected <= allowed:
        raise argparse.ArgumentTypeError('허용 값: ' + ', '.join(sorted(allowed)))
    return selected


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
    status.add_argument('--full',action='store_true',help='기존 상세 목록 출력')
    status.add_argument('--status',type=lambda value: choices(value, STATUSES),help='쉼표로 구분한 상태')
    status.add_argument('--category',type=lambda value: choices(value, CATEGORIES),help='쉼표로 구분한 분류')
    status.add_argument('--search',help='ID·제목·작업 내용에서 대소문자 구분 없이 검색')
    status.add_argument('--limit',type=positive,default=10)
    status.add_argument('--offset',type=offset,default=0)
    search=sub.add_parser('search',help='작업·결정·검증·체크포인트 검색')
    search.add_argument('query');search.add_argument('--limit',type=positive,default=5)
    search.add_argument('--status',type=lambda value: choices(value,STATUSES))
    search.add_argument('--category',type=lambda value: choices(value,CATEGORIES))
    from .records import KINDS
    search.add_argument('--kind',choices=sorted(KINDS));search.add_argument('--all',action='store_true',help='보관 작업 포함')
    search.add_argument('--all-projects',action='store_true',help='등록된 프로젝트 전체 검색')
    index=sub.add_parser('index',help='기존 기록을 검색 구조로 이관·재색인')
    index.add_argument('--rebuild',action='store_true');index.add_argument('--all-projects',action='store_true')
    record=sub.add_parser('record').add_subparsers(dest='record_action',required=True)
    record_add=record.add_parser('add');record_add.add_argument('task_id');record_add.add_argument('--kind',choices=['decision','verification','note'],required=True)
    body=record_add.add_mutually_exclusive_group(required=True);body.add_argument('--text');body.add_argument('--file',type=Path)
    record_list=record.add_parser('list');record_list.add_argument('task_id');record_list.add_argument('--kind',choices=sorted(KINDS));record_list.add_argument('--section')
    record_list.add_argument('--limit',type=positive,default=5);record_list.add_argument('--offset',type=offset,default=0)
    record_show=record.add_parser('show');record_show.add_argument('id');record_show.add_argument('--full',action='store_true')
    task=sub.add_parser('task').add_subparsers(dest='action',required=True)
    add=task.add_parser('add'); add.add_argument('title'); add.add_argument('--category',default='task'); add.add_argument('--json',dest='input')
    show=task.add_parser('show'); show.add_argument('id');show.add_argument('--full',action='store_true');show.add_argument('--section')
    update=task.add_parser('update'); update.add_argument('id'); update.add_argument('--json',dest='input',required=True)
    start=task.add_parser('start'); start.add_argument('id'); start.add_argument('--version',required=True,type=int)
    check=sub.add_parser('checkpoint'); check.add_argument('task_id'); check.add_argument('--session'); check.add_argument('--json',dest='input',required=True)
    resume=sub.add_parser('resume'); resume.add_argument('task_id'); resume.add_argument('--session')
    for command in (add, update, start, check, resume):
        command.add_argument('--full',action='store_true',help='요약 대신 전체 결과 출력')
    sessions=sub.add_parser('sessions'); sessions.add_argument('--all',action='store_true');sessions.add_argument('--full',action='store_true');sessions.add_argument('--limit',type=positive,default=10);sessions.add_argument('--offset',type=offset,default=0)
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
    usage=sub.add_parser('usage',help='수집된 실행 사용량 조회');usage.add_argument('--goal');usage.add_argument('--role');usage.add_argument('--full',action='store_true',help='최근 100개 실행 내역 포함')
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
    if args.command in {'search','index'} and args.all_projects:
        results=[];errors=[]
        from .records import Records
        for item in projects(getattr(args,'all',False)):
            try:
                if args.command=='index':
                    if item.get('status')=='archived':
                        results.append({'project':item['id'],'status':'readonly'});continue
                    result=Records(Store(item['db_path'])).ensure(args.rebuild)
                else:
                    result=Records(Store(item['db_path'],readonly=True)).search(args.query,args.limit,args.status,args.category,args.kind,args.all)
                results.append({'project':fields(item,('id','name')),**result})
            except (BoardError,OSError,sqlite3.Error) as error:
                errors.append({'project':item['id'],'error':str(error)})
        if args.command=='index':
            emit({'projects':results,'errors':errors})
            if errors:raise BoardError('일부 프로젝트 색인에 실패했습니다. errors를 확인해주세요.',409)
            return
        matches=[{**m,'project':r['project']} for r in results for m in r['matches']]
        matches.sort(key=lambda m:(sum(t.casefold() in m['title'].casefold() for t in args.query.split()),m['created_at']),reverse=True)
        return emit({'query':args.query,'matches':matches[:args.limit],'has_more':len(matches)>args.limit or any(r['has_more'] for r in results),'errors':errors})
    project=by_id(args.project) if args.project else resolve(args.workspace,create=args.command in {'init','migrate'} or (args.command=='task' and args.action=='add') or (args.command=='goal' and args.goal_action in {'add','draft'}) or (args.command=='team' and args.team_action=='create'),
        local=getattr(args,'local',False),name=getattr(args,'name',None),link=getattr(args,'link',None),source_roots=getattr(args,'source_root',None))
    if args.command=='migrate':
        from .migration import migrate
        return emit(migrate(project,args.board,args.notes,args.active_note))
    readonly=(args.command=='settings' and args.settings_action in {'show','catalog'}) or args.command in {'status','resume','sessions','doctor','usage','search'} or (args.command=='record' and args.record_action!='add') or (args.command=='goal' and args.goal_action in {'list','show','messages'}) or (args.command=='team' and args.team_action in {'list','show','collaboration'}) or (args.command=='document' and args.document_action!='import') or (args.command=='task' and args.action=='show')
    from .project_lifecycle import read
    if not readonly and args.command!='dashboard' and read(project['db_path'])['status']=='archived':raise BoardError('보관 프로젝트는 읽기 전용입니다. project restore로 복원해주세요.',409)
    store=Store(project['db_path'],readonly=readonly)
    if args.command in {'bind','doctor'}:
        from .tracking import Tracker
        tracker=Tracker(store,project['workspace'])
        if args.command=='bind':
            tracker.bind(args.thread,args.task_id,args.expected_task);return emit({'thread_id':args.thread,'task_id':args.task_id})
        return emit({'workspace':project['workspace'],'data_path':str(store.path),'received_hook_events':len(tracker.overview()['observations']),'orchestration':False})
    if args.command in {'goal','settings','team'}:
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
    if args.command=='usage':
        from .usage import Usage
        result=Usage(store).overview(args.goal,args.role)
        if not args.full:result.pop('runs')
        return emit(result)
    from .records import Records, bounded
    records=Records(store)
    if args.command=='index':return emit(records.ensure(args.rebuild))
    if args.command=='search':return emit({'project':fields(project,('id','name')),**records.search(args.query,args.limit,args.status,args.category,args.kind,args.all)})
    if args.command=='record':
        if args.record_action=='add':
            text=args.text if args.text is not None else args.file.read_text()
            return emit(records.add(args.task_id,args.kind,text))
        if args.record_action=='list':return emit(records.list(args.task_id,args.kind,args.section,args.limit,args.offset))
        return emit(records.show(args.id,args.full))
    if args.command=='status':
        tasks=store.all() if args.full or args.search else records.heads()
        if args.status:
            tasks=[t for t in tasks if t['status'] in args.status]
        elif not args.all:
            tasks=[t for t in tasks if t['status'] not in {'done','archived'}]
        if args.category:
            tasks=[t for t in tasks if t['category'] in args.category]
        if args.search:
            term=args.search.casefold()
            tasks=[t for t in tasks if any(term in t[key].casefold() for key in TEXT_FIELDS | {'id'})]
        sessions=store.sessions(args.all)
        if args.status or args.category or args.search:
            ids={t['id'] for t in tasks}
            sessions=[r for r in sessions if r['task_id'] in ids]
        task_keys=('id','title','category','status','version','blocked_by')
        session_keys=('id','task_id','status','branch','version','updated_at')
        if args.full:
            return emit({'project':project,'tasks':[fields(t,task_keys+('next_action',)) for t in tasks],
                         'sessions':[fields(r,('id','task_id','status','branch','next_action','updated_at')) for r in sessions]})
        counts={status:sum(t['status']==status for t in tasks) for status in sorted({t['status'] for t in tasks})}
        return emit({'project':fields(project,('id','name','version')),'counts':counts,
                     'tasks':[fields(t,task_keys) for t in sorted(tasks,key=lambda t:(t['status']=='in_progress',t['updated_at']),reverse=True)[args.offset:args.offset+args.limit]],'sessions':[fields(r,session_keys) for r in sessions[:args.limit]],'total_tasks':len(tasks),'total_sessions':len(sessions),'limit':args.limit,'offset':args.offset,'has_more':args.offset+args.limit<len(tasks) or args.limit<len(sessions)})
    if args.command=='task':
        if args.action=='show':
            if args.section:
                rows=records.list(args.id,section=args.section,limit=1)['records']
                if not rows:raise BoardError('해당 본문 레코드가 없습니다.',404)
                return emit(records.show(rows[0]['id'],args.full))
            return emit(store.detail(args.id) if args.full else records.overview(args.id))
        if args.action=='add': return emit(write_result(store.write({'title':args.title,'category':args.category,**(payload(args.input) if args.input else {})}),args.full))
        if args.action=='update': return emit(write_result(store.write(payload(args.input),args.id),args.full))
        task=store.detail(args.id)['task']
        if task['status'] in {'done','archived'}: raise BoardError('완료·보관된 작업은 명시적으로 상태를 수정한 뒤 시작하세요.')
        if any(store.detail(key)['task']['status']!='done' for key in task['blocked_by']): raise BoardError('미완료 선행 작업이 있습니다.')
        return emit(write_result(store.write({'version':args.version,'status':'in_progress','note':'작업 시작'},args.id),args.full))
    if args.command=='checkpoint':
        data=payload(args.input)
        data.setdefault('workspace',project['workspace'])
        data.setdefault('branch',git_value(project['workspace'],'branch','--show-current'))
        data.setdefault('thread_id',os.environ.get('CODEX_THREAD_ID',''))
        data.setdefault('agent','codex' if os.environ.get('CODEX_THREAD_ID') else 'cli')
        data['task_id']=args.task_id
        return emit(write_result(store.checkpoint(data,args.session),args.full,'session'))
    if args.command=='resume':
        if args.full:return emit(store.resume(args.task_id,args.session))
        result=records.overview(args.task_id)
        sessions=store.sessions()
        if args.session:
            selected=store.session(args.session)['session']
            if selected['task_id']!=args.task_id:raise BoardError('작업에 연결되지 않은 세션입니다.')
            sessions=[selected]
        sessions=[r for r in sessions if r['task_id']==args.task_id]
        result['sessions']=[{**fields(r,('id','task_id','version','status')), 'goal':bounded(r['goal'],240),'next_action':bounded(r['next_action'],480),'decisions':bounded(r['decisions'],480),'truncated_fields':[k for k,n in (('goal',240),('next_action',480),('decisions',480)) if len(r[k])>n]} for r in sessions[:3]]
        result['total_sessions']=len(sessions)
        heads={t['id']:t for t in records.heads()}
        result['dependencies']=[fields(heads[key],('id','title','status','version')) for key in result['task']['blocked_by']]
        return emit(result)
    if args.command=='sessions':
        sessions=store.sessions(args.all)
        if args.full:return emit({'sessions':sessions})
        return emit({'sessions':[{**fields(r,('id','task_id','status','version','updated_at')), 'goal':bounded(r['goal'],160),'next_action':bounded(r['next_action'],240),'truncated_fields':[k for k,n in (('goal',160),('next_action',240)) if len(r[k])>n]} for r in sessions[args.offset:args.offset+args.limit]],'total':len(sessions),'offset':args.offset,'has_more':args.offset+args.limit<len(sessions)})
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
