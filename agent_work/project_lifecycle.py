"""Project archive/restore and scoped deletion; shared by CLI, HTTP and Store."""
from contextlib import contextmanager, closing
import fcntl
import json
from pathlib import Path
import shutil
import sqlite3
from .store import BoardError, timestamp

KEY='project_lifecycle'
DEFAULT={'status':'active','version':1}

def state(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='metadata'").fetchone():return dict(DEFAULT)
    row=db.execute('SELECT value FROM metadata WHERE key=?',(KEY,)).fetchone()
    return json.loads(row[0]) if row else dict(DEFAULT)

def read(path):
    path=Path(path)
    if not path.exists():return dict(DEFAULT)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=10)) as db:
        return state(db)

@contextmanager
def access(path,exclusive=False,create=True):
    """Use a sidecar lock; macOS flock on the DB conflicts with SQLite locks."""
    lock_path=Path(str(path)+'.lifecycle-lock')
    if not lock_path.exists() and not create:
        yield
        return
    with lock_path.open('rb' if lock_path.exists() else 'a+b') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB if exclusive else fcntl.LOCK_SH)
        except BlockingIOError:raise BoardError('프로젝트 저장소를 사용 중입니다. 실행 종료 후 다시 시도해주세요.',409)
        try:yield
        finally:fcntl.flock(lock,fcntl.LOCK_UN)

def backups(project):
    parent=Path(project['db_path']).resolve().parent
    paths=[p for p in parent.iterdir() if p.name in {'backups','evidence'} or p.name.startswith('migration-')]
    return sorted(paths)

def idle(db):
    for table in ('sessions','goals','executions'):
        if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():continue
        for row in db.execute('SELECT data FROM '+table):
            data=json.loads(row[0])
            busy=(data.get('worker') or data.get('status') in {'running','verifying'}) if table=='goals' else data.get('status')==('active' if table=='sessions' else 'running')
            if busy:raise BoardError('활성 세션 또는 실행 중인 목표가 있습니다. 실제 종료를 확인하고 세션 완료/목표 중단·복구 후 다시 시도해주세요.',409)

def preview(project):
    path=Path(project['db_path']).resolve()
    if not path.is_file():raise BoardError('프로젝트 DB가 없습니다.',404)
    result={**project,**read(path),'counts':{},'backup_paths':[str(p) for p in backups(project)] if path.parent.exists() else [],
            'preserved':['프로젝트 소스 폴더','Codex·Orca 원본 대화','프로젝트 외부에 복사한 백업','설치된 프로젝트 하네스']}
    with access(path,create=False),closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=10)) as db:
        try:
            for table in ['tasks','sessions','goals','executions','documents','observations']:
                if db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():result['counts'][table]=db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
            try:idle(db);result['can_archive']=True
            except BoardError as error:result['can_archive']=False;result['blocked_reason']=str(error)
        finally:db.close()
    return result

def change(project,action,payload):
    if action not in {'archive','restore','delete'}:raise BoardError('프로젝트 작업을 확인해주세요.')
    allowed={'version','confirmation','include_backups'} if action=='delete' else {'version'}
    if not isinstance(payload,dict) or set(payload)-allowed or type(payload.get('version')) is not int:raise BoardError('최신 프로젝트 version이 필요합니다.')
    path=Path(project['db_path']).resolve()
    # Only tool-owned data directories may be cleaned, never the source folder.
    from .projects import home
    raw=Path(project['db_path']).absolute()
    if raw.is_symlink() or raw.parent!=raw.parent.resolve() or path.parent not in {home()/'projects'/project['id'],Path(project['workspace']).resolve()/'.agent-work'}:raise BoardError('프로젝트 관리 저장 경로가 아닙니다. 삭제·보관을 중단했습니다.',409)
    if not path.is_file():raise BoardError('프로젝트 DB를 찾을 수 없습니다.',404)
    with access(path,exclusive=True),closing(sqlite3.connect(path,timeout=10)) as db:
        try:
            db.execute('BEGIN EXCLUSIVE')
            current=state(db)
            if current['status']=='deleted':raise BoardError('이미 삭제한 프로젝트입니다.',410)
            if current['version']!=payload['version']:raise BoardError('프로젝트 상태가 변경됐습니다. 새로고침 후 확인해주세요.',409)
            desired='archived' if action=='archive' else 'active' if action=='restore' else 'deleted'
            if desired==current['status']:raise BoardError('이미 해당 상태인 프로젝트입니다.',409)
            if action!='restore':idle(db)
            removed=[]
            if action=='delete':
                if current['status']!='archived':raise BoardError('프로젝트를 먼저 보관한 뒤 영구 삭제해주세요.',409)
                if payload.get('confirmation')!=project['name'] or type(payload.get('include_backups')) is not bool:raise BoardError('프로젝트 이름과 백업 삭제 여부를 확인해주세요.')
                if payload['include_backups']:
                    try:
                        for target in backups(project):
                            if target.is_symlink() or target.is_file():target.unlink()
                            else:shutil.rmtree(target)
                            removed.append(str(target))
                    except OSError:raise BoardError('백업 삭제 중 실패했습니다. 일부 백업은 삭제됐을 수 있으며 DB 기록은 유지했습니다. 권한을 확인 후 다시 시도해주세요.',503)
                db.execute('UPDATE metadata SET value=? WHERE key=?',(json.dumps({'status':'deleting','version':current['version']}),KEY))
                tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                for table in tables:db.execute('DELETE FROM "'+table.replace('"','""')+'"')
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='metadata'")]
            for table in tables:
                quoted='"'+table.replace('"','""')+'"'
                for operation in ('INSERT','UPDATE','DELETE'):
                    name='"tack_lifecycle_'+operation+'_'+table.replace('"','""')+'"'
                    db.execute('CREATE TRIGGER IF NOT EXISTS '+name+' BEFORE '+operation+' ON '+quoted+" WHEN json_extract((SELECT value FROM metadata WHERE key='project_lifecycle'),'$.status') IN ('archived','deleted') BEGIN SELECT RAISE(ABORT,'보관 또는 삭제한 프로젝트는 기록할 수 없습니다.'); END")
            remaining=[str(p) for p in backups(project)]
            updated={'status':desired,'version':current['version']+1,'updated_at':timestamp()}
            db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',(KEY,json.dumps(updated)))
            db.commit()
            compact_error=''
            if action=='delete':
                try:db.execute('VACUUM')
                except sqlite3.Error:compact_error='기록은 삭제됐으나 DB 공간 회수에 실패했습니다. 백업·디스크 여유를 확인해주세요.'
            return {**project,**updated,'warning':compact_error,'removed_backup_paths':removed,'remaining_backup_paths':remaining}
        finally:db.close()
