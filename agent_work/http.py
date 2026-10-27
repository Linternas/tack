"""Loopback UI/API over the same Store used by the CLI."""
import json
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from .store import BoardError, Store, timestamp
from .projects import by_id, projects

ASSETS=Path(__file__).parent/'assets'

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
            project=self.server.project
            if query.get('project',[project['id']])[0] != project['id']:
                project=by_id(query['project'][0])
            store=self.server.store if project['id']==self.server.project['id'] else Store(project['db_path'])
            path=parsed.path
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
                if path=='/api/tasks' and self.command=='POST':
                    return self.respond(store.write(payload),201)
                if re.fullmatch(r'/api/tasks/[A-Z0-9-]+',path) and self.command=='PATCH':
                    return self.respond(store.write(payload,path.rsplit('/',1)[1]))
                if path=='/api/sessions' and self.command=='POST':
                    return self.respond(store.checkpoint(payload),201)
                if re.fullmatch(r'/api/sessions/RUN-[A-Z0-9]+',path) and self.command=='PATCH':
                    return self.respond(store.checkpoint(payload,path.rsplit('/',1)[1]))
                raise BoardError('없는 저장 경로입니다.',404)
            if path=='/api/context':
                registered=projects() if 'db_path' in project and not (Path(project['workspace'])/'.agent-work/project.json').exists() else [project]
                return self.respond({'project':project,'projects':[{'id':p['id'],'name':p['name'],'workspace':p['workspace']} for p in registered]})
            if path=='/api/projects':
                registered=projects() if not (Path(project['workspace'])/'.agent-work/project.json').exists() else [project]
                summaries=[]
                for item in registered:
                    summary={k:item[k] for k in ('id','name','workspace')}
                    try:
                        other=Store(item['db_path'],readonly=True)
                        tasks=other.all()
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
            assets={'/':'index.html','/index.html':'index.html','/qa':'qa.html','/qa/':'qa.html','/qa.html':'qa.html','/sessions':'sessions.html','/projects':'projects.html'}
            name=assets.get(path,path.lstrip('/'))
            if name not in {'index.html','qa.html','sessions.html','dashboard.js','dashboard.css','qa.js','qa.css','common.js','sessions.js','projects.html','projects.js'}:
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
