"""Loopback UI/API over the same Store used by the CLI."""
import json
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from .store import BoardError, Store, timestamp
from .projects import by_id, projects, registered_workspaces
from .project_lifecycle import read as project_state, preview as project_preview, change as project_change

ASSETS=Path(__file__).parent/'assets'
HTML_PREVIEW_HEADERS = {
    'Content-Security-Policy': "sandbox; default-src 'none'; style-src 'unsafe-inline'; "
                               "img-src data:; font-src data:; frame-ancestors 'self'; "
                               "base-uri 'none'; form-action 'none'",
    'Referrer-Policy': 'no-referrer',
    'X-DNS-Prefetch-Control': 'off',
}



class BoardServer(ThreadingHTTPServer):
    daemon_threads=True

    def __init__(self,address,store,project=None):
        self.store=store
        self.project=project or {'id':'test','name':'Test','workspace':str(Path.cwd()),'source_roots':['docs','specs','.vscode/plans']}
        super().__init__(address,Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def respond(self,value,status=200,mime='application/json; charset=utf-8',extra=None):
        body=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode()
        self.send_response(status)
        for key,value in {'Content-Type':mime,'Content-Length':str(len(body)),'Cache-Control':'no-store',
            'X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",**(extra or {})}.items():
            self.send_header(key,value)
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self,write=False):
        try:
            host=self.headers.get('Host','')
            if host not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}:
                raise BoardError('로컬 주소로 접속해주세요.',403)
            parsed=urlsplit(self.path)
            query=parse_qs(parsed.query)
            path=parsed.path
            project=self.server.project
            lifecycle_route=re.fullmatch(r'/api/projects/([^/]+)(?:/(archive|restore|delete))?',path)
            if lifecycle_route:
                project=self.server.project if lifecycle_route[1]==self.server.project['id'] else by_id(lifecycle_route[1])
            if not lifecycle_route and path not in {'/api/projects','/projects'} and query.get('project',[project['id']])[0] != project['id']:
                project=by_id(query['project'][0])
            selected_workspace = query.get('workspace')
            if selected_workspace and not lifecycle_route and path not in {'/api/projects', '/projects'}:
                if len(selected_workspace) != 1 or not registered_workspaces(project, selected_workspace[0]):
                    raise BoardError('이 프로젝트에 등록된 작업 폴더를 선택해주세요.',404)
                project={**project,'workspace':selected_workspace[0]}
            map_route = re.fullmatch(r'/api/tasks/([A-Z0-9-]+)/map', path)
            execution_route = re.fullmatch(r'/api/executions/([^/]+)', path)
            if not write and (path in {'/api/executions', '/api/usage'} or execution_route or map_route):
                store = Store(project.get('db_path', self.server.store.path), readonly=True)
                if map_route:
                    from .task_map import get_map
                    return self.respond(get_map(store, project['workspace'], map_route[1]))
                if path == '/api/usage':
                    from .usage import Usage
                    return self.respond(Usage(store).overview(query.get('goal',[None])[0],query.get('role',[None])[0]))
                from .execution_history import detail, history
                if execution_route:
                    return self.respond(detail(store, unquote(execution_route[1])))
                return self.respond(history(store, int(query.get('limit', ['20'])[0]), int(query.get('offset', ['0'])[0])))
            store=self.server.store if project['id']==self.server.project['id'] else Store(project['db_path'])
            project={**project,**project_state(project.get('db_path',self.server.store.path))}
            note_route=re.fullmatch(r'/api/notes/(NOTE-[A-F0-9]{32})',path)
            if write:
                origin=self.headers.get('Origin')
                if (origin and origin!=f'http://{host}') or self.headers.get('Sec-Fetch-Site')=='cross-site':
                    raise BoardError('같은 로컬 화면에서 저장해주세요.',403)
                if self.headers.get('Content-Type','').split(';',1)[0].strip()!='application/json':
                    raise BoardError('JSON 요청이 필요합니다.',415)
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=1024*1024:
                    raise BoardError('저장 내용의 크기를 확인해주세요.',413)
                payload=json.loads(self.rfile.read(length))
                if lifecycle_route and lifecycle_route[2] and self.command=='POST':
                    return self.respond(project_change(project,lifecycle_route[2],payload))
                if project['status']=='archived':raise BoardError('보관 프로젝트는 읽기 전용입니다. 프로젝트를 복원해주세요.',409)
                if project['status']=='deleted':raise BoardError('삭제한 프로젝트입니다.',410)
                relation_route=re.fullmatch(r'/api/tasks/([A-Z0-9-]+)/relations',path)
                if relation_route and self.command=='POST':
                    if not isinstance(payload,dict) or set(payload)-{'target','type','version','remove'} or not {'target','type','version'}<=set(payload) or type(payload.get('remove',False)) is not bool:
                        raise BoardError('관계 유형·대상·최신 version을 확인해주세요.')
                    return self.respond(store.link(relation_route[1],payload['target'],payload['type'],payload['version'],payload.get('remove',False)))
                if path=='/api/worktrees/remove' and self.command=='POST':
                    from .projects import remove_worktree
                    return self.respond(remove_worktree(project['workspace'],store,payload))
                if path=='/api/notes' and self.command=='POST':
                    from .notes import save_note
                    return self.respond(save_note(store,payload),201)
                if note_route and self.command=='PATCH':
                    from .notes import save_note
                    return self.respond(save_note(store,payload,note_route[1]))
                if path=='/api/tasks' and self.command=='POST':
                    return self.respond(store.write(payload),201)
                if re.fullmatch(r'/api/tasks/[A-Z0-9-]+',path) and self.command=='PATCH':
                    return self.respond(store.write(payload,path.rsplit('/',1)[1]))
                if path=='/api/sessions' and self.command=='POST':
                    return self.respond(store.checkpoint(payload),201)
                if re.fullmatch(r'/api/sessions/RUN-[A-Z0-9]+',path) and self.command=='PATCH':
                    return self.respond(store.checkpoint(payload,path.rsplit('/',1)[1]))
                raise BoardError('없는 저장 경로입니다.',404)
            if lifecycle_route and not lifecycle_route[2]:return self.respond(project_preview(project))
            if path=='/api/context':
                registered=projects() if 'db_path' in project and not (Path(project['workspace'])/'.agent-work/project.json').exists() else [project]
                registered=[p for p in registered if p.get('status','active')!='deleted']
                if project['status']=='archived' and not any(p['id']==project['id'] for p in registered):registered.append(project)
                rows = [{'id':p['id'],'name':p['name'],'workspace':p['workspace'],
                         'status':p.get('status','active')} for p in registered]
                return self.respond({'project':project,'projects':rows})
            if path=='/api/projects':
                registered=projects(include_archived=query.get('all')==['1']) if not (Path(project['workspace'])/'.agent-work/project.json').exists() else [project]
                registered=[p for p in registered if p.get('status','active')!='deleted' and (query.get('all')==['1'] or p.get('status','active')!='archived')]
                summaries=[]
                for item in registered:
                    summary={k:item[k] for k in ('id','name','workspace')}
                    summary.update(status=item.get('status','active'),version=item.get('version',1))
                    try:
                        summary.update(project_state(item['db_path']))
                        other=Store(item['db_path'],readonly=True)
                        tasks=other.all()
                        from .tracking import Tracker
                        try:
                            from .project_lifecycle import idle
                            with other.connect() as db:idle(db)
                            summary['can_archive']=True
                        except BoardError as error:summary.update(can_archive=False,blocked_reason=str(error))
                        summary.update(hook_events=len(Tracker(other,item['workspace']).overview()['observations']),workspace_exists=Path(item['workspace']).is_dir())
                        summary.update(open_tasks=sum(t['status'] not in {'done','archived'} for t in tasks),done_tasks=sum(t['status']=='done' for t in tasks),active_sessions=len(other.sessions()))
                    except (BoardError,OSError,sqlite3.Error):
                        summary['error']='저장소를 읽을 수 없습니다.'
                    summaries.append(summary)
                return self.respond({'projects':summaries})
            relation_route=re.fullmatch(r'/api/tasks/([A-Z0-9-]+)/relations',path)
            if relation_route:
                return self.respond(store.relationships(relation_route[1],limit=int(query.get('limit',['10'])[0]),offset=int(query.get('offset',['0'])[0])))
            if path=='/api/notes':
                from .notes import list_notes
                return self.respond(list_notes(store,query.get('q',[''])[0][:300],
                                               max(0,min(1000000,int(query.get('offset',['0'])[0])))))
            if note_route:
                from .notes import get_note
                return self.respond(get_note(store,note_route[1]))
            if path=='/api/tasks':
                return self.respond({'tasks':store.all(),'saved_at':timestamp()})
            if re.fullmatch(r'/api/tasks/[A-Z0-9-]+',path):
                return self.respond(store.detail(path.rsplit('/',1)[1]))
            if path=='/api/worktrees':
                from .projects import worktrees
                items=worktrees(project['workspace'],store.sessions(include_completed=True),include_activity=True)
                if project['status']=='archived':
                    for item in items:
                        item['removable']=False
                        item['remove_reasons'].append('프로젝트가 보관 상태입니다.')
                return self.respond({'worktrees':items})
            if path=='/api/sessions':
                return self.respond({'sessions':store.sessions(query.get('all')==['1'])})
            if re.fullmatch(r'/api/sessions/RUN-[A-Z0-9]+',path):
                return self.respond(store.session(path.rsplit('/',1)[1]))
            if re.fullmatch(r'/api/resume/[A-Z0-9-]+',path):
                return self.respond(store.resume(path.rsplit('/',1)[1]))
            if path=='/api/export':
                from .notes import export_notes
                return self.respond({'format':2,'exported_at':timestamp(),'tasks':store.all(),'sessions':store.sessions(True),'notes':export_notes(store)},extra={'Content-Disposition':'attachment; filename="tack.json"'})
            if path=='/api/documents':
                return self.respond({'documents':store.documents()})
            if re.fullmatch(r'/api/documents/DOC-[A-Z0-9]+',path):
                return self.respond(store.document(path.rsplit('/',1)[1]))
            if path in {'/api/source','/source'} or re.fullmatch(r'/documents/DOC-[A-Z0-9]+',path):
                from .documents import source_document,render_document,source_format
                key='archive:'+path.rsplit('/',1)[1] if path.startswith('/documents/') else query.get('path',[''])[0]
                document=source_document(store,project,key)
                if path=='/api/source':
                    return self.respond({'path':key,'text':document['text'],'format':source_format(document)})
                if path=='/source' and query.get('preview')==['1']:
                    if source_format(document)!='html':raise BoardError('HTML 문서만 이 미리보기에서 열 수 있습니다.',415)
                    return self.respond(document['text'].encode(),mime='text/html; charset=utf-8',extra=HTML_PREVIEW_HEADERS)
                return self.respond(render_document(store,project,document),mime='text/html; charset=utf-8')
            if path=='/documents':
                from .documents import render_index
                return self.respond(render_index(store,project),mime='text/html; charset=utf-8')
            assets={'/notes':'notes.html','/':'index.html','/index.html':'index.html','/qa':'qa.html','/qa/':'qa.html','/qa.html':'qa.html','/sessions':'sessions.html','/projects':'projects.html','/usage':'usage.html'}
            name=assets.get(path,path.lstrip('/'))
            if name not in {'favicon.svg','notes.html','notes.js','index.html','qa.html','sessions.html','dashboard.js','dashboard.css','qa.js','qa.css','common.js','sessions.js','projects.html','projects.js','usage.html','usage.js','usage.css'}:
                raise BoardError('경로를 찾을 수 없습니다.',404)
            mime={'.html':'text/html','.css':'text/css','.js':'application/javascript','.svg':'image/svg+xml'}[Path(name).suffix]
            return self.respond((ASSETS/name).read_bytes(),mime=mime+'; charset=utf-8')
        except BoardError as error:
            self.respond({'error':str(error)},error.status)
        except (ValueError,UnicodeError,TypeError):
            self.respond({'error':'입력 형식을 확인해주세요.'},400)
        except sqlite3.Error:
            self.respond({'error':'저장소 처리에 실패했습니다. 입력을 유지한 채 다시 시도해주세요.'},503)
        except OSError:
            self.respond({'error':'파일을 읽을 수 없습니다.'},500)

    def do_GET(self): self.dispatch()
    def do_POST(self): self.dispatch(True)
    def do_PATCH(self): self.dispatch(True)
