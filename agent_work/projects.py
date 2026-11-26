"""Stable project identities, explicit storage selection, and checkout aliases."""
import json
import os
import re
import sqlite3
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from .store import BoardError


def home():
    return Path(os.environ.get('AGENT_WORK_HOME', Path.home()/'.local/share/agent-work')).expanduser().resolve()


def git_value(workspace, *args):
    result = subprocess.run(['git','-C',str(workspace),*args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ''


def worktree_git(workspace, *args, timeout=5):
    try:
        return subprocess.run(
            ['git', '-C', str(workspace), *args], capture_output=True, timeout=timeout,
            env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'},
        )
    except FileNotFoundError:
        raise BoardError('Git 실행 파일을 찾을 수 없습니다.', 503) from None
    except subprocess.TimeoutExpired:
        raise BoardError('Git 워크트리 상태 조회 시간이 초과됐습니다.', 503) from None


def worktree_status(path):
    status = worktree_git(path, 'status', '--porcelain=v1', '-z', '--no-renames',
                          '--untracked-files=all', '--ignored=matching', '--ignore-submodules=none')
    upstream = worktree_git(path, 'rev-parse', '--symbolic-full-name', '@{upstream}')
    unpushed = None
    if upstream.returncode == 0 and upstream.stdout.startswith(b'refs/remotes/'):
        ahead = worktree_git(path, 'rev-list', '--count', '@{upstream}..HEAD')
        if ahead.returncode == 0:
            unpushed = int(ahead.stdout)
    entries = status.stdout.split(b'\0')[:-1]
    ignored_paths = []
    for entry in entries:
        if not entry.startswith(b'!! '):
            continue
        ignored = entry[3:]
        name = ignored.rstrip(b'/').rsplit(b'/', 1)[-1]
        generated_dir = ignored.endswith(b'/') and name in {b'node_modules', b'.next', b'.swc'}
        if generated_dir or name == b'tsconfig.tsbuildinfo' or ignored.startswith(b'.husky/_/'):
            continue
        ignored_paths.append(ignored.decode('utf-8', 'backslashreplace'))
    return {
        'uncommitted': sum(not entry.startswith(b'!! ') for entry in entries) if status.returncode == 0 else None,
        'ignored_paths': sorted(ignored_paths),
        'unpushed': unpushed,
    }


def worktree_reflog_time(path):
    try:
        result = worktree_git(path, 'reflog', '-1', '--date=iso-strict', '--format=%gD', 'HEAD')
        if result.returncode == 0:
            selector = result.stdout.decode('ascii').strip()
            if selector.startswith('HEAD@{') and selector.endswith('}'):
                return datetime.fromisoformat(selector[6:-1]).isoformat()
    except (BoardError, ValueError):
        pass
    return ''


def worktrees(workspace, sessions, *, include_activity=False):
    result = worktree_git(workspace, 'worktree', 'list', '--porcelain', '-z')
    if result.returncode:
        return []

    current = Path(workspace).resolve()
    open_paths = {
        Path(session['workspace']).resolve()
        for session in sessions
        if session['status'] in {'active', 'blocked'} and Path(session.get('workspace') or '.').is_absolute()
    }
    active_paths = {
        Path(session['workspace']).resolve()
        for session in sessions
        if session['status'] == 'active' and Path(session.get('workspace') or '.').is_absolute()
    }
    last_session = {}
    if include_activity:
        for session in sessions:
            session_path = Path(session.get('workspace') or '.')
            if session_path.is_absolute() and session.get('updated_at'):
                key = session_path.resolve()
                last_session[key] = max(last_session.get(key, ''), session['updated_at'])
    items = []
    for index, record in enumerate(result.stdout.split(b'\0\0')):
        fields = {}
        for field in record.split(b'\0'):
            key, _, value = field.partition(b' ')
            if key:
                fields[key.decode('ascii')] = value
        if 'prunable' in fields or 'bare' in fields or 'worktree' not in fields:
            continue
        display_path = fields['worktree'].decode('utf-8', 'replace')
        path = Path(os.fsdecode(fields['worktree'])).resolve()
        stats = worktree_status(path)
        reasons = []
        if index == 0:
            reasons.append('기본 워크트리입니다.')
        if 'locked' in fields:
            reasons.append('잠긴 워크트리입니다.')
        if any(session_path.is_relative_to(path) for session_path in open_paths):
            reasons.append('열린 Tack 세션이 있습니다.')
        if display_path.encode('utf-8') != fields['worktree'] or stats['uncommitted'] is None:
            reasons.append('폴더의 변경 상태를 확인할 수 없습니다.')
        elif stats['uncommitted']:
            reasons.append(f"미커밋 파일이 {stats['uncommitted']}개 있습니다.")
        if stats['unpushed']:
            reasons.append(f"미푸시 커밋이 {stats['unpushed']}개 있습니다.")
        head = fields.get('HEAD', b'').decode('ascii')
        branch_ref = fields.get('branch', b'')
        retained_by = ''
        backup_branch = ''
        if not reasons:
            git_dir = worktree_git(path, 'rev-parse', '--absolute-git-dir')
            if git_dir.returncode:
                reasons.append('Git 작업 상태를 확인할 수 없습니다.')
            else:
                metadata = Path(os.fsdecode(git_dir.stdout.rstrip(b'\n')))
                if any((metadata / name).exists() for name in (
                    'rebase-merge', 'rebase-apply', 'MERGE_HEAD', 'CHERRY_PICK_HEAD',
                    'REVERT_HEAD', 'BISECT_START', 'sequencer',
                )):
                    reasons.append('리베이스·병합 등 진행 중인 Git 작업이 있습니다.')
        if not reasons and stats['unpushed'] is None:
            if not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', head) or not head.strip('0'):
                reasons.append('보존할 커밋을 확인할 수 없습니다.')
            else:
                refs = worktree_git(path, 'for-each-ref', '--contains', head,
                                    '--format=%(refname)%09%(symref)', 'refs/heads', 'refs/remotes')
                if refs.returncode:
                    reasons.append('커밋을 포함하는 브랜치를 확인할 수 없습니다.')
                else:
                    for row in refs.stdout.splitlines():
                        ref, _, symbolic = row.partition(b'\t')
                        if not symbolic and ref != branch_ref:
                            retained_by = ref.split(b'/', 2)[-1].decode('utf-8', 'replace')
                            break
                    if not retained_by:
                        backup_branch = 'tack/archive/' + head
        items.append({
            'path': display_path,
            'branch': fields.get('branch', b'').removeprefix(b'refs/heads/').decode('utf-8', 'replace'),
            'head': head[:7],
            'commit': head,
            'retained_by': retained_by,
            'backup_branch': backup_branch,
            'current': current.is_relative_to(path),
            'active': path in active_paths,
            **stats,
            'remove_reasons': reasons,
        })
        if include_activity:
            session_time = last_session.get(path, '')
            items[-1]['last_session_at'] = session_time
            items[-1]['last_git_at'] = '' if session_time else worktree_reflog_time(path)
    # Git worktrees can be nested inside the main checkout.
    current_item = max((item for item in items if item['current']), key=lambda item: len(item['path']), default=None)
    for item in items:
        item['current'] = item is current_item
        if item['current']:
            item['remove_reasons'].append('현재 보고 있는 워크트리입니다.')
        path = Path(item['path'])
        if any(other is not item and Path(other['path']).is_relative_to(path) for other in items):
            item['remove_reasons'].append('하위 경로에 다른 워크트리가 있습니다.')
        item['removable'] = not item['remove_reasons']
    return items


def remove_worktree(workspace, store, payload):
    if (not isinstance(payload, dict) or set(payload) - {'path', 'ignored_paths', 'commit', 'backup_branch'}
            or not isinstance(payload.get('path'), str) or '\0' in payload['path']):
        raise BoardError('삭제할 워크트리 경로를 확인해주세요.')
    from .project_lifecycle import state
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if state(db)['status'] != 'active':
            raise BoardError('활성 프로젝트에서만 워크트리를 삭제할 수 있습니다.', 409)
        item = next((row for row in worktrees(workspace, store.sessions())
                     if row['path'] == payload['path']), None)
        if not item:
            raise BoardError('목록에서 워크트리를 찾을 수 없습니다.', 409)
        if not item['removable']:
            raise BoardError(' '.join(item['remove_reasons']), 409)
        confirmed = payload.get('ignored_paths', [])
        if not isinstance(confirmed, list) or any(not isinstance(path, str) for path in confirmed):
            raise BoardError('확인한 무시 파일 목록이 잘못되었습니다.')
        if set(item['ignored_paths']) - set(confirmed):
            raise BoardError('확인하지 않은 Git 무시 파일 또는 폴더가 있습니다.', 409)
        if ('commit' in payload or item['unpushed'] is None) and payload.get('commit') != item['commit']:
            raise BoardError('체크아웃 커밋을 다시 확인해주세요.', 409)
        if payload.get('backup_branch', '') != item['backup_branch']:
            raise BoardError('커밋 보관 조건이 달라졌습니다. 목록을 다시 확인해주세요.', 409)
        backup_branch = item['backup_branch']
        if backup_branch:
            saved = worktree_git(workspace, 'branch', '--', backup_branch, item['commit'])
            if saved.returncode:
                raise BoardError('보관용 브랜치를 만들지 못해 삭제를 중단했습니다.', 409)
        # Killing Git during removal can leave a partially deleted checkout.
        result = worktree_git(workspace, 'worktree', 'remove', '--', item['path'], timeout=None)
        if result.returncode:
            preserved = f' 보관용 브랜치 {backup_branch}은 유지됐습니다.' if backup_branch else ''
            raise BoardError('Git이 워크트리 삭제를 거부했습니다. 폴더의 상태를 다시 확인해주세요.' + preserved, 409)
    return {'removed': True, **({'backup_branch': backup_branch} if backup_branch else {})}


def identity(workspace):
    workspace = Path(workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise BoardError('프로젝트 폴더가 존재하지 않습니다.')
    git_root=git_value(workspace,'rev-parse','--show-toplevel')
    root=Path(git_root).resolve() if git_root else next((folder for folder in (workspace,*workspace.parents) if (folder/'.agent-work/project.json').is_file()),workspace)
    if not git_root:
        for parent in workspace.parents:
            if not workspace.is_relative_to(parent/'.tack-orchestration/workspaces'):continue
            marker=parent/'.tack-orchestration/install.json'
            if marker.is_file():
                try:installed=json.loads(marker.read_text())
                except (OSError,ValueError):installed={}
                if installed.get('format')==1 and installed.get('workspace')==str(parent):root=parent;break
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


def registered_workspaces(project, workspace=None):
    """List the live, canonical checkout roots registered to this project's store."""
    if (Path(project['workspace'])/'.agent-work/project.json').is_file():
        candidates = [project['workspace']]
    else:
        if not (home()/'registry.sqlite3').is_file():
            return []
        sql = 'SELECT workspace FROM projects WHERE id=? UNION SELECT path FROM aliases WHERE project_id=?'
        values = (project['id'], project['id'])
        if workspace is not None:
            sql = f'SELECT workspace FROM ({sql}) WHERE workspace=?'
            values += (workspace,)
        with registry(readonly=True) as db:
            candidates = [row[0] for row in db.execute(sql, values)]
    workspaces = []
    for candidate in candidates:
        path = Path(candidate)
        try:
            if (workspace is not None and candidate != workspace or not path.is_absolute() or '.git' in path.parts
                or not path.is_dir() or path.resolve() != path):
                continue
            resolved = resolve(path)
            if (resolved['workspace'] == candidate and resolved['id'] == project['id']
                and resolved['db_path'] == project['db_path']
                and git_value(path, 'rev-parse', '--is-inside-git-dir') != 'true'):
                workspaces.append(candidate)
        except (BoardError, OSError, ValueError, sqlite3.Error):
            continue
    return sorted(workspaces)


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
