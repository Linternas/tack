"""Loopback UI/API over the same Store used by the CLI."""
import json
from datetime import datetime
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from .store import BoardError, Store, timestamp
from .projects import by_id, projects
from .project_lifecycle import read as project_state, preview as project_preview, change as project_change

ASSETS=Path(__file__).parent/'assets'

class BoardServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,store,project=None,allow_execution=False,harness=False):
        self.harness=harness
        self.allow_execution=allow_execution and harness
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
            store=self.server.store if project['id']==self.server.project['id'] else Store(project['db_path'])
            project={**project,**project_state(project.get('db_path',self.server.store.path))}
            extension=path.startswith(('/api/goals','/api/team/','/api/orchestration')) or path in {'/goals','/goals.html','/goals.js','/goals.css'}
            if extension:
                if not self.server.harness:raise BoardError('프로젝트 로컬 오케스트레이션에서만 제공됩니다.',404)
                from .harness import require_enabled
                require_enabled(project['workspace'])
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
                if path=='/api/team/settings' and self.command=='PATCH':
                    from .team import Team
                    return self.respond(Team(store,project['workspace']).save(payload))
                if re.fullmatch(r'/api/goals/GOAL-[A-Z0-9]+/messages',path) and self.command=='POST':
                    from .team import Team
                    from .orchestration import Orchestrator
                    from .orchestration_cli import start
                    if not isinstance(payload,dict):raise BoardError('메시지 객체가 필요합니다.')
                    if payload.get('kind','note')!='note' and not self.server.allow_execution:raise BoardError('현재 서버는 조회 모드입니다.',403)
                    key=path.split('/')[-2]
                    result=Team(store).send(Orchestrator(store,project['workspace']),key,payload)
                    if result.get('execute'):result['launch']=start(project,key,result['version'])
                    return self.respond(result,201)
                if path=='/api/goals/draft' and self.command=='POST':
                    from .orchestration import Orchestrator
                    from .orchestration_cli import start
                    if not self.server.allow_execution: raise BoardError('현재 서버는 실행을 허용하지 않습니다.',403)
                    if not isinstance(payload,dict): raise BoardError('목표 객체가 필요합니다.')
                    goal=Orchestrator(store,project['workspace']).draft(payload.get('title'),{k:v for k,v in payload.items() if k!='title'})
                    return self.respond({**goal,'launch':start(project,goal['id'],goal['version'])},201)
                if path=='/api/goals' and self.command=='POST':
                    from .orchestration import Orchestrator
                    if not isinstance(payload,dict): raise BoardError('목표 객체가 필요합니다.')
                    return self.respond(Orchestrator(store,project['workspace']).create(payload.get('title'),{k:v for k,v in payload.items() if k!='title'}),201)
                if re.fullmatch(r'/api/goals/GOAL-[A-Z0-9]+/(start|pause)',path) and self.command=='POST':
                    from .orchestration import Orchestrator
                    from .orchestration_cli import start
                    if not self.server.allow_execution: raise BoardError('이 서버의 실행 제어는 꺼져 있습니다. --allow-execution으로 명시적으로 시작하세요.',403)
                    if not isinstance(payload,dict) or set(payload)!={'version'} or type(payload['version']) is not int: raise BoardError('최신 version이 필요합니다.')
                    key,action=path.split('/')[-2:]
                    result=start(project,key,payload['version'],approve=True) if action=='start' else Orchestrator(store,project['workspace']).pause(key,payload['version'])
                    return self.respond(result)
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
            if path=='/api/team/catalog':
                from .crewset import catalog
                return self.respond(catalog())
            if path=='/api/team/settings':
                from .team import Team
                return self.respond(Team(store).settings())
            if re.fullmatch(r'/api/goals/GOAL-[A-Z0-9]+/chat',path):
                from .team import conversation
                from .orchestration import Orchestrator
                return self.respond({**conversation(Orchestrator(store,project['workspace']),path.split('/')[-2]),'execution_enabled':self.server.allow_execution and project['status']=='active'})
            if path=='/api/orchestration':
                from .orchestration import Orchestrator
                if query.get('all')==['1']:
                    combined={'goals':[],'runs':[],'observations':[],'errors':[]}
                    registered=projects() if not (Path(project['workspace'])/'.agent-work/project.json').exists() else [project]
                    from .harness import enabled
                    for item in registered:
                        if not enabled(item['workspace']):continue
                        try:
                            overview=Orchestrator(Store(item['db_path'],readonly=True),item['workspace']).overview()
                            for kind in ('goals','runs','observations'):
                                combined[kind].extend({**row,'project_id':item['id'],'project_name':item['name']} for row in overview[kind])
                        except (OSError,BoardError,sqlite3.Error):combined['errors'].append(item['name'])
                    combined['goals'].sort(key=lambda row:(row['status'] in {'done','archived'}, -datetime.fromisoformat(row['updated_at']).timestamp()))
                    combined['runs']=sorted(combined['runs'],key=lambda row:row['created_at'],reverse=True)[:100]
                    combined['observations']=sorted(combined['observations'],key=lambda row:row['created_at'],reverse=True)[:100]
                    return self.respond({**combined,'execution_enabled':self.server.allow_execution and project['status']=='active','scope':'all'})
                tasks=store.all()
                return self.respond({**Orchestrator(store,project['workspace']).overview(),'execution_enabled':self.server.allow_execution and project['status']=='active','scope':'project',
                    'project':{'id':project['id'],'name':project['name'],'workspace':project['workspace']},'open_tasks':sum(t['status'] not in {'done','archived'} for t in tasks)})
            if path=='/api/context':
                registered=projects() if 'db_path' in project and not (Path(project['workspace'])/'.agent-work/project.json').exists() else [project]
                registered=[p for p in registered if p.get('status','active')!='deleted']
                if self.server.harness:
                    from .harness import enabled
                if project['status']=='archived' and not any(p['id']==project['id'] for p in registered):registered.append(project)
                return self.respond({'project':project,'orchestration_enabled':self.server.harness,'projects':[{'id':p['id'],'name':p['name'],'workspace':p['workspace'],'status':p.get('status','active'),'orchestration_enabled':enabled(p['workspace']) if self.server.harness else False} for p in registered]})
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
                        with other.connect() as db:
                            count=db.execute('SELECT COUNT(*) FROM goals').fetchone()[0] if db.execute("SELECT 1 FROM sqlite_master WHERE name='goals'").fetchone() else 0
                        try:
                            from .project_lifecycle import idle
                            with other.connect() as db:idle(db)
                            summary['can_archive']=True
                        except BoardError as error:summary.update(can_archive=False,blocked_reason=str(error))
                        summary.update(goal_count=count,hook_events=len(Tracker(other,item['workspace']).overview()['observations']),workspace_exists=Path(item['workspace']).is_dir())
                        summary.update(open_tasks=sum(t['status'] not in {'done','archived'} for t in tasks),done_tasks=sum(t['status']=='done' for t in tasks),active_sessions=len(other.sessions()))
                    except (BoardError,OSError,sqlite3.Error):
                        summary['error']='저장소를 읽을 수 없습니다.'
                    summaries.append(summary)
                return self.respond({'projects':summaries})
            if path=='/api/tasks':
                return self.respond({'tasks':store.all(),'saved_at':timestamp()})
            if re.fullmatch(r'/api/tasks/[A-Z0-9-]+',path):
                return self.respond(store.detail(path.rsplit('/',1)[1]))
            if path=='/api/sessions':
                return self.respond({'sessions':store.sessions(query.get('all')==['1'])})
            if re.fullmatch(r'/api/sessions/RUN-[A-Z0-9]+',path):
                return self.respond(store.session(path.rsplit('/',1)[1]))
            if re.fullmatch(r'/api/resume/[A-Z0-9-]+',path):
                return self.respond(store.resume(path.rsplit('/',1)[1]))
            if path=='/api/export':
                return self.respond({'format':2,'exported_at':timestamp(),'tasks':store.all(),'sessions':store.sessions(True)},extra={'Content-Disposition':'attachment; filename="tack.json"'})
            if path=='/api/documents':
                return self.respond({'documents':store.documents()})
            if re.fullmatch(r'/api/documents/DOC-[A-Z0-9]+',path):
                return self.respond(store.document(path.rsplit('/',1)[1]))
            if path in {'/api/source','/source'} or re.fullmatch(r'/documents/DOC-[A-Z0-9]+',path):
                from .documents import source_document,render_document
                key='archive:'+path.rsplit('/',1)[1] if path.startswith('/documents/') else query.get('path',[''])[0]
                document=source_document(store,project,key)
                if path=='/api/source': return self.respond({'path':key,'text':document['text']})
                return self.respond(render_document(store,project,document),mime='text/html; charset=utf-8')
            if path=='/documents':
                from .documents import render_index
                return self.respond(render_index(store,project),mime='text/html; charset=utf-8')
            assets={'/':'index.html','/index.html':'index.html','/qa':'qa.html','/qa/':'qa.html','/qa.html':'qa.html','/sessions':'sessions.html','/projects':'projects.html','/goals':'goals.html'}
            name=assets.get(path,path.lstrip('/'))
            if name not in {'index.html','qa.html','sessions.html','dashboard.js','dashboard.css','qa.js','qa.css','common.js','sessions.js','projects.html','projects.js','goals.html','goals.js','goals.css'}:
                raise BoardError('경로를 찾을 수 없습니다.',404)
            mime={'.html':'text/html','.css':'text/css','.js':'application/javascript'}[Path(name).suffix]
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
