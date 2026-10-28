"""Stable project identities, explicit storage selection, and checkout aliases."""
import json
import os
import sqlite3
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path
from .store import BoardError


def home():
    return Path(os.environ.get('AGENT_WORK_HOME', Path.home()/'.local/share/agent-work')).expanduser().resolve()


def git_value(workspace, *args):
    result = subprocess.run(['git','-C',str(workspace),*args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ''


def identity(workspace):
    workspace = Path(workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise BoardError('프로젝트 폴더가 존재하지 않습니다.')
    git_root=git_value(workspace,'rev-parse','--show-toplevel')
    root=Path(git_root).resolve() if git_root else next((folder for folder in (workspace,*workspace.parents) if (folder/'.agent-work/project.json').is_file()),workspace)
    common = git_value(root,'rev-parse','--path-format=absolute','--git-common-dir')
    return root, str(Path(common).resolve()) if common else str(root)


@contextmanager
def registry(readonly=False):
    folder = home()
    if not readonly:
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path=folder/'registry.sqlite3'
    if readonly and not path.exists():
        raise BoardError('등록된 프로젝트가 없습니다. tack init 또는 tack task add로 등록하세요.',404)
    db = sqlite3.connect(path.as_uri()+'?mode=ro' if readonly else path, timeout=10, uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=10000')
    if not readonly:
        db.executescript('''CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, workspace TEXT NOT NULL, db_path TEXT NOT NULL, source_roots TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS aliases (path TEXT PRIMARY KEY, project_id TEXT NOT NULL);''')
    try:
        with db:
            yield db
    finally:
        db.close()


def decode(row, workspace=None):
    value = dict(row)
    value['source_roots'] = json.loads(value['source_roots']) if isinstance(value['source_roots'],str) else value['source_roots']
    from .project_lifecycle import read
    try:value.update(read(value['db_path']))
    except sqlite3.Error:value.update(status='unavailable',version=0)
    if workspace:
        value['workspace'] = str(workspace)
    return value


def projects(include_archived=False):
    if not (home()/'registry.sqlite3').exists():
        return []
    with registry(readonly=True) as db:
        result=[decode(row) for row in db.execute('SELECT * FROM projects ORDER BY name,id')]
        return [p for p in result if p['status']!='deleted' and (include_archived or p['status']!='archived')]


def by_id(project_id,include_deleted=False):
    with registry(readonly=True) as db:
        row = db.execute('SELECT * FROM projects WHERE id=?',(project_id,)).fetchone()
        if not row:
            raise BoardError('프로젝트 ID를 찾을 수 없습니다.',404)
        project=decode(row)
        if project['status']=='deleted' and not include_deleted:raise BoardError('삭제한 프로젝트입니다.',410)
        return project


def local_project(root):
    marker = root/'.agent-work/project.json'
    if not marker.exists():
        return None
    data = json.loads(marker.read_text())
    if not isinstance(data.get('id'),str) or not data['id']:
        raise BoardError('로컬 프로젝트 설정을 확인해주세요.')
    filename=data.get('db_file','work.sqlite3')
    if not isinstance(filename,str) or Path(filename).name!=filename or not filename.endswith('.sqlite3'):raise BoardError('로컬 DB 파일명을 확인해주세요.')
    from .project_lifecycle import read
    return {**read(root/'.agent-work'/filename),'id':data['id'],'name':data.get('name',root.name),'workspace':str(root),
            'db_path':str(root/'.agent-work'/data.get('db_file','work.sqlite3')),'source_roots':data.get('source_roots',[])}


def resolve(workspace, create=False, local=False, name=None, link=None, source_roots=None):
    root, common = identity(workspace)
    # A local marker is explicit and never silently falls back to a global DB.
    existing_local = local_project(root)
    if existing_local:
        if existing_local['status']=='deleted':
            if not create:raise BoardError('삭제한 프로젝트입니다. tack init으로 새 프로젝트를 등록하세요.',410)
            new_id=str(uuid.uuid4());marker=root/'.agent-work/project.json'
            marker.write_text(json.dumps({'id':new_id,'name':name or root.name,'source_roots':source_roots or [],'db_file':'work-'+new_id+'.sqlite3'},ensure_ascii=False,indent=2)+'\n')
            return local_project(root)
        if link:
            raise BoardError('로컬 저장 프로젝트는 전역 프로젝트와 중복 연결할 수 없습니다.')
        return existing_local
    if local:
        if link:
            raise BoardError('--local과 --link는 함께 사용할 수 없습니다.')
        reg = home()/'registry.sqlite3'
        if reg.exists():
            with sqlite3.connect(f'{reg.as_uri()}?mode=ro',uri=True) as db:
                if db.execute('SELECT 1 FROM aliases WHERE path IN (?,?)',(str(root),common)).fetchone():
                    raise BoardError('이미 전역에 등록된 프로젝트입니다. 자동으로 별도 DB를 만들지 않습니다.')
        folder=root/'.agent-work'
        folder.mkdir(mode=0o700)
        (folder/'project.json').write_text(json.dumps({'id':str(uuid.uuid4()),'name':name or root.name,'source_roots':source_roots or []},ensure_ascii=False,indent=2)+'\n')
        (folder/'.gitignore').write_text('*\n')
        return local_project(root)
    mutating=bool(create or name or link or source_roots is not None)
    with registry(readonly=not mutating) as db:
        if mutating:
            db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT p.* FROM aliases a JOIN projects p ON p.id=a.project_id WHERE a.path=?',(str(root),)).fetchone()
        if not row:
            row = db.execute('SELECT p.* FROM aliases a JOIN projects p ON p.id=a.project_id WHERE a.path=?',(common,)).fetchone()
        if row and decode(row)['status']=='deleted':
            if not create:raise BoardError('삭제한 프로젝트입니다. tack init으로 새 프로젝트를 등록하세요.',410)
            db.execute('DELETE FROM aliases WHERE project_id=?',(row['id'],));row=None
        if row:
            if link and link != row['id']:
                raise BoardError('이미 다른 프로젝트에 연결된 경로입니다.')
            if name or source_roots is not None:
                db.execute('UPDATE projects SET name=?,source_roots=? WHERE id=?',(name or row['name'],json.dumps(source_roots) if source_roots is not None else row['source_roots'],row['id']))
                row=db.execute('SELECT * FROM projects WHERE id=?',(row['id'],)).fetchone()
            if mutating:
                db.execute('INSERT OR IGNORE INTO aliases VALUES (?,?)',(str(root),row['id']))
            return decode(row,root)
        if link:
            row=db.execute('SELECT * FROM projects WHERE id=?',(link,)).fetchone()
            if not row or decode(row)['status']=='deleted':
                raise BoardError('연결할 프로젝트를 찾을 수 없습니다.',404)
            db.execute('INSERT INTO aliases VALUES (?,?)',(str(root),link))
            db.execute('INSERT OR IGNORE INTO aliases VALUES (?,?)',(common,link))
            # An explicit link also updates the preferred root after a move.
            db.execute('UPDATE projects SET workspace=? WHERE id=?',(str(root),link))
            return decode(row,root)
        if not create:
            raise BoardError('등록되지 않은 프로젝트입니다. tack init 또는 tack task add로 등록하세요.',404)
        project_id=str(uuid.uuid4())
        path=home()/'projects'/project_id/'work.sqlite3'
        values=(project_id,name or root.name,str(root),str(path),json.dumps(source_roots or []))
        db.execute('INSERT INTO projects VALUES (?,?,?,?,?)',values)
        db.execute('INSERT INTO aliases VALUES (?,?)',(str(root),project_id))
        db.execute('INSERT OR IGNORE INTO aliases VALUES (?,?)',(common,project_id))
        return {**dict(zip(('id','name','workspace','db_path','source_roots'),(*values[:-1],source_roots or []))),'status':'active','version':1}
